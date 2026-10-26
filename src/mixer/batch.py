"""批量调度：探测 → 镜头分析 → 计划 → 并发渲染 → 封面 → 报告；支持停止、断点续跑、失败重试。

事件回调 callback(event, data)：
  log       {"msg", "level"}
  stage     {"text"}                      整体阶段说明
  progress  {"done","failed","total","pct"}
  item      {"index","name","status","stage","pct","file","error"}
  finished  {"ok","failed","skipped","total","dir","elapsed","stopped"}
"""
import csv
import datetime
import json
import os
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import VERSION, ffmpeg, paths, probe, scenes, utils, dedup
from . import render as rd
from .models import MixConfig, save_json, load_json
from .planner import Planner, PlanItem, safe_name


def history_file():
    return paths.data_path("history.json")


def load_history():
    return load_json(history_file(), []) or []


def add_history(entry):
    hist = [h for h in load_history() if h.get("dir") != entry.get("dir")]
    hist.insert(0, entry)
    save_json(history_file(), hist[:200])


class BatchRunner:
    def __init__(self, cfg: MixConfig = None, callback=None, resume_dir=None, record_history=True):
        self.cfg = cfg
        self.record_history = record_history
        self.cb = callback or (lambda ev, data: None)
        self.resume_dir = resume_dir
        self.cancel = threading.Event()     # 立即中止（结束 ffmpeg）
        self.soft = threading.Event()       # 完成当前后停止
        self.lock = threading.Lock()
        self.manifest = {}
        self.plans = []
        self.infos = {}
        self.dup = None                     # 剪辑前查重索引
        self._solo_dup = {}                 # 复查时按需单独分析的素材索引
        self._src_h = {}                    # 复查确认用：素材原始画面指纹
        self._plan_changed = False          # 复查时整段移除过素材
        self.batch_dir = None
        self._logfh = None
        self.item_frac = {}
        self.done = 0
        self.failed = 0
        self.skipped = 0
        self.total = 0
        self.results = {}

    # ------------------------------------------------------------ 基础
    def emit(self, ev, **data):
        try:
            self.cb(ev, data)
        except Exception:
            pass

    def log(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        with self.lock:
            if self._logfh:
                try:
                    self._logfh.write(line + "\n")
                    self._logfh.flush()
                except Exception:
                    pass
        if level != "debug":
            self.emit("log", msg=msg, level=level)

    def stop(self, immediate=False):
        self.soft.set()
        if immediate:
            self.cancel.set()

    def stage(self, text):
        self.emit("stage", text=text)
        self.log(text)

    # ------------------------------------------------------------ 目录
    def _make_batch_dir(self):
        root = self.cfg.output_root or paths.default_output_root()
        os.makedirs(root, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = safe_name(self.cfg.name or "混剪", 30)
        base = os.path.join(root, f"混剪输出_{ts}_{name}_{len(self.cfg.materials)}素材_目标{self.cfg.count}条")
        d = base
        i = 2
        while os.path.exists(d):
            d = f"{base}_{i}"
            i += 1
        for sub in ("成片", "数据", "_tmp"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        if self.cfg.cover:
            os.makedirs(os.path.join(d, "封面"), exist_ok=True)
        return d

    def _p(self, *parts):
        return os.path.join(self.batch_dir, *parts)

    def _save_manifest(self):
        with self.lock:
            save_json(self._p("数据", "manifest.json"), self.manifest)

    # ------------------------------------------------------------ 准备
    def _probe_all(self):
        cfg = self.cfg
        files = list(dict.fromkeys(cfg.materials + cfg.openings + cfg.endings + cfg.bgms))
        self.stage(f"正在检查 {len(files)} 个素材文件…")
        done = [0]

        def each(info):
            done[0] += 1
            if not info.ok:
                self.log(f"! 跳过无效素材：{info.name}（{info.error}）", level="warn")
            if done[0] % 10 == 0:
                self.emit("stage", text=f"正在检查素材 {done[0]}/{len(files)}…")
        self.infos = probe.probe_many(files, workers=6, on_each=each)
        ok = [p for p in cfg.materials if self.infos.get(p) and self.infos[p].ok]
        self.log(f"素材检查完成：可用 {len(ok)}/{len(cfg.materials)}")
        if not ok:
            raise RuntimeError("没有可用的视频/图片素材，请检查素材文件。")

    def _scan_dedup(self):
        """剪辑前查重：分析全部素材的画面指纹（片段模式下顺带做镜头切点，一遍解码两用）。"""
        cfg = self.cfg
        plist = list(dict.fromkeys(cfg.materials + cfg.openings + cfg.endings))
        infos = [self.infos[p] for p in plist if self.infos.get(p) and self.infos[p].ok
                 and self.infos[p].kind in ("video", "image")]
        if not infos:
            return None
        need = set()
        if cfg.use_scenes and cfg.mode == "segment":
            need = {p for p in cfg.materials if self.infos.get(p) and self.infos[p].ok and self.infos[p].kind == "video"}
        self.stage(f"正在素材查重（{len(infos)} 个素材，识别剪辑拼接素材里的重复画面，结果会缓存）…")

        def prog(d, n):
            self.emit("stage", text=f"正在素材查重 {d}/{n}…")
        idx = dedup.analyze_library(infos, cfg.dedup_level, need_scenes=need, cancel=self.cancel, progress=prog,
                                    log=self.log, workers=min(4, max(1, (os.cpu_count() or 4) // 4)))
        for p, q, r in idx.full_dups:
            self.log(f"! 素材「{os.path.basename(p)}」与「{os.path.basename(q)}」{r:.0%} 画面相同", level="warn")
        for pa, a0, a1, pb, b0, b1 in idx.runs[:30]:
            if a1 > a0:
                self.log(f"  重复画面：{os.path.basename(pa)} {utils.fmt_secs(a0)}~{utils.fmt_secs(a1)} ≈ "
                         f"{os.path.basename(pb)} {utils.fmt_secs(b0)}~{utils.fmt_secs(b1)}", level="debug")
        try:
            save_json(self._p("数据", "素材查重.json"), {
                "level": cfg.dedup_level, "summary": idx.summary(),
                "runs": [{"素材A": a, "A起": a0, "A止": a1, "素材B": b, "B起": b0, "B止": b1}
                         for a, a0, a1, b, b0, b1 in idx.runs],
                "几乎完全重复": [{"素材": p, "等同于": q, "比例": r} for p, q, r in idx.full_dups],
                "重复占比": {p: round(r, 3) for p, r in idx.ratio.items() if r > 0}})
        except OSError:
            pass
        return idx

    def _render_item(self, item, ctx, out, job_dir, setp, weight_parts):
        """渲染一条成片的全部片段（交给全局片段线程池并行，单条长成片也能吃满可用 CPU）并合成，返回片段文件列表。"""
        parts = rd.item_parts(item, ctx)
        n = len(parts)
        fracs = [0.0] * n
        done_n = [0]

        def cbp(fr, k):
            fracs[k] = fr
            setp(weight_parts * sum(fracs) / max(1, n), f"渲染片段 {min(n, done_n[0] + 1)}/{n}")

        def job(k, spec):
            if self.cancel.is_set():
                raise ffmpeg.Cancelled()
            f = rd.render_part(spec, ctx, on_progress=lambda fr: cbp(fr, k))
            done_n[0] += 1
            return f
        futs = [self.part_pool.submit(job, k, spec) for k, spec in enumerate(parts)]
        files = []
        try:
            for f in futs:
                files.append(f.result())
        except BaseException:
            for f in futs:
                f.cancel()
            raise
        rd.compose(item, files, out, self.cfg, ctx, job_dir=job_dir,
                   on_progress=lambda fr: setp(weight_parts + (1 - weight_parts) * 0.9 * fr, "合成成片"))
        return files

    def _whole_dups(self, item, out, ctx):
        """整段拼接复查：成片里两个不同素材之间仍有重复画面时，返回要整段移除的素材序号（后出现的那个，
        片头片尾保留）；同一个素材内部的重复不处理（整段拼接不拆素材）。"""
        segs = rd.output_segments(item, ctx.fps)

        def seg_at(t):
            for k, (o0, o1, _s) in enumerate(segs):
                if o0 <= t < o1:
                    return k
            return None
        fixed = ("opening", "ending")
        drop = set()
        for r in dedup.check_output(probe.probe(out, use_cache=False), self.cfg.dedup_level):
            ka, kb = seg_at((r[0] + r[1]) / 2), seg_at((r[2] + r[3]) / 2)
            if ka is None or kb is None or ka == kb or not self._real_dup(segs, r):
                continue
            later, other = max(ka, kb), min(ka, kb)
            if segs[later][2].kind in fixed:
                later = other
            if segs[later][2].kind not in fixed:
                drop.add(later)
        if len(drop) >= len(segs) - sum(1 for _a, _b, s in segs if s.kind in fixed):
            return []   # 全移除就没有内容了，保留原样
        return sorted(drop)

    @staticmethod
    def _drop_segments(item, drop, fps):
        """从成片计划里整段移除若干素材（相邻处沿用原来的转场），并更新时长。"""
        from .planner import Seg, total_frames
        keep = [k for k in range(len(item.segments)) if k not in drop]
        trans = list(item.transitions or [])
        new_trans = []
        for a, b in zip(keep, keep[1:]):
            k = a if b == a + 1 else b - 1
            new_trans.append(trans[k] if k < len(trans) else "")
        item.segments = [item.segments[k] for k in keep]
        item.transitions = new_trans
        nfr = total_frames([Seg(**x) for x in item.segments], new_trans, item.trans_frames,
                           getattr(item, "trans_hold", False))
        item.duration = round(nfr / float(fps), 3)

    def _index_for(self, pa, pb):
        """包含这两个素材的查重索引：优先用剪辑前查重的结果；没有时（关闭了剪辑前查重、断点续跑、片头片尾）
        只对这两个素材建索引（指纹有缓存，很快）。"""
        if self.dup is not None and pa in self.dup.ids and pb in self.dup.ids:
            return self.dup
        key = (pa, pb) if pa <= pb else (pb, pa)
        with self.lock:
            if key in self._solo_dup:
                return self._solo_dup[key]
        media = {}
        for path in set(key):
            d = self._src_hash(path)
            if d is not None:
                media[path] = dict(d, kind=self.infos[path].kind)
        idx = None
        if len(media) == len(set(key)):
            try:
                idx = dedup.build_index(media, self.cfg.dedup_level)
            except Exception:
                idx = None
        with self.lock:
            self._solo_dup[key] = idx
        return idx

    def _src_hash(self, path):
        """素材原始画面的指纹（剪辑前查重时已算过，有缓存）。"""
        with self.lock:
            if path in self._src_h:
                return self._src_h[path]
        d = None
        info = self.infos.get(path)
        if info is not None and info.ok and info.kind in ("video", "image"):
            try:
                d = dedup.analyze_media(info)
            except Exception:
                d = None
        with self.lock:
            self._src_h[path] = d
        return d

    def _real_dup(self, segs, rep):
        """复查候选（原成片时间）是否真重复。成片画面经过画布适配（模糊背景/黑边）、叠加了贴纸标题，
        画面细节少时不同内容也可能“看起来像”，所以逐个采样点换算回素材时刻再确认：
          - 同一素材同一时刻，或查重索引认定为同一画面（连续多帧比对，可靠）；
          - 不同素材原始画面的指纹确实相近（含镜像）。
        至少 2 个采样点、且过半确认才算重复；同一镜头不同时刻的相似画面（口播、固定机位）不算。"""
        a0, a1, b0, b1 = rep
        L = dedup.LEVELS.get(self.cfg.dedup_level, dedup.LEVELS["standard"])
        T = L["T"] + 2

        def src_at(t):
            hit = None
            for o0, o1, s in segs:
                if o0 <= t < o1:
                    hit = (s, o0)       # 转场重叠时取后出现的片段
            if hit is None:
                return None
            s, o0 = hit
            return s.src, s.start + (t - o0) * s.speed

        def bin_of(path, t):
            d = self._src_hash(path)
            if d is None or d["n"] == 0:
                return None, -1
            return d, min(d["n"] - 1, max(0, int(round(t / dedup.BIN))))
        ka = range(int(round(a0 / dedup.BIN)), int(round(a1 / dedup.BIN)))
        kb = range(int(round(b0 / dedup.BIN)), int(round(b1 / dedup.BIN)))
        valid = same = 0
        for i, j in zip(ka, kb):
            sa, sb = src_at((i + 0.5) * dedup.BIN), src_at((j + 0.5) * dedup.BIN)
            if sa is None or sb is None:
                continue
            (pa, ta), (pb, tb) = sa, sb
            da, ia = bin_of(pa, ta)
            db, ib = bin_of(pb, tb)
            if da is None or db is None or da["low"][ia] or db["low"][ib]:
                continue
            valid += 1
            if pa == pb and abs(ta - tb) < dedup.BIN:
                same += 1               # 同一素材同一时刻：同一段画面用了两次
                continue
            idx = self._index_for(pa, pb)
            h = dedup.BIN / 2           # 采样点前后各半格，容忍对齐误差
            if idx is not None and idx.ids_in(pa, ta - h, ta + h) & idx.ids_in(pb, tb - h, tb + h):
                same += 1               # 查重索引认定的同一画面
                continue
            if pa != pb:
                # 不同素材，原始画面确实相同（拼接素材里的片段不一定从整 0.5 秒开始，允许相邻采样点对齐）
                near = [k for k in (ib - 1, ib, ib + 1) if 0 <= k < db["n"] and not db["low"][k]]
                if near and min(min(dedup.hamming(da["h"][ia], db["h"][k]), dedup.hamming(da["h"][ia], db["hf"][k]))
                                for k in near) <= T:
                    same += 1
        if valid == 0:
            return True                 # 换算不了（没有计划信息等）时按复查结果处理
        return same >= 2 and same * 2 >= valid

    def _postcheck(self, item, out, ctx):
        """剪辑后复查：成片内部若仍有重复画面，删除后出现的那一段；删除后再查，最多 3 轮。"""
        cfg = self.cfg
        bounds = rd.output_boundaries(item, ctx.fps)
        segs = rd.output_segments(item, ctx.fps)
        removed = []      # 已删除的区间（原成片时间轴，有序、不重叠）
        skipped = [0]

        def orig(t):      # 当前时间轴 → 原成片时间轴
            for a, b in removed:
                if t >= a:
                    t += b - a
                else:
                    break
            return t

        def real_reps(info):
            reps = []
            for r in dedup.check_output(info, cfg.dedup_level):
                if self._real_dup(segs, tuple(orig(x) for x in r)):
                    reps.append(r)
                else:
                    skipped[0] += 1
            return reps
        removed_total = 0.0
        for rnd in range(3):
            info = probe.probe(out, use_cache=False)
            reps = real_reps(info)
            if not reps:
                if skipped[0]:
                    self.log(f"成片复查：{item.name} 有 {skipped[0]} 处画面相似、但对照素材原始画面并非同一内容，未删除",
                             level="debug")
                return
            # 第一轮可以对齐到片段边界；之后时间轴已变化，按实际检测位置剪
            if rnd == 0:
                def snap(t):
                    near = min(bounds, key=lambda x: abs(x - t)) if bounds else t
                    return near if abs(near - t) <= 0.6 else t
            else:
                def snap(t):
                    return t
            cuts = dedup.merge_ranges([(snap(b0), snap(b1)) for _a0, _a1, b0, b1 in reps])
            cuts = [(a, min(b, info.duration)) for a, b in cuts if b - a >= 0.3]
            total = sum(b - a for a, b in cuts)
            desc = "；".join(f"{utils.fmt_secs(b0)}~{utils.fmt_secs(b1)} 与 {utils.fmt_secs(a0)}~{utils.fmt_secs(a1)} 相同"
                             for a0, a1, b0, b1 in reps[:4])
            if not cuts or removed_total + total >= item.duration * 0.5:
                self.log(f"! 成片复查：{item.name} 疑似有重复画面（{desc}），重复过多未自动删除，请人工检查",
                         level="warn")
                return
            new_dur = rd.remove_ranges(out, cuts, cfg, ctx, info.duration)
            removed = dedup.merge_ranges(removed + [(orig(a), orig(b)) for a, b in cuts], gap=0.0)
            removed_total += total
            self.log(f"成片复查：{item.name} 发现重复画面（{desc}），已删除 {total:.1f} 秒，"
                     f"时长 {info.duration:.1f}s → {new_dur:.1f}s")
            item.duration = new_dur
        if real_reps(probe.probe(out, use_cache=False)):
            self.log(f"! 成片复查：{item.name} 多轮删除后仍有疑似重复画面，请人工检查", level="warn")

    def _scan_scenes(self):
        cfg = self.cfg
        vids = [self.infos[p] for p in cfg.materials if self.infos.get(p) and self.infos[p].ok
                and self.infos[p].kind == "video"]
        if not (cfg.use_scenes and cfg.mode == "segment" and vids):
            return {}
        self.stage(f"正在分析镜头切点（{len(vids)} 个视频，结果会缓存，下次更快）…")
        out = {}
        n = [0]

        def work(info):
            if self.cancel.is_set():
                return info.path, []
            cuts = scenes.detect(info)
            n[0] += 1
            self.emit("stage", text=f"正在分析镜头切点 {n[0]}/{len(vids)}…")
            return info.path, cuts
        with ThreadPoolExecutor(max_workers=min(4, max(1, (os.cpu_count() or 4) // 4))) as ex:
            for path, cuts in ex.map(work, vids):
                out[path] = cuts
        total = sum(len(v) for v in out.values())
        self.log(f"镜头分析完成：共识别 {total} 个切点")
        return out

    def prepare(self):
        """新任务：探测、规划、建目录；续跑：载入原计划。"""
        if self.resume_dir:
            self.batch_dir = self.resume_dir
            task = load_json(self._p("数据", "task.json"), {})
            if not task:
                raise RuntimeError("该目录不是有效的任务目录（缺少 数据/task.json）")
            self.cfg = MixConfig.from_dict(task.get("config"))
            plan_data = load_json(self._p("数据", "plan.json"), {})
            self.plans = [PlanItem.from_dict(d) for d in plan_data.get("items", [])]
            self.canvas = tuple(plan_data.get("canvas") or self.cfg.canvas_size())
            self.manifest = load_json(self._p("数据", "manifest.json"), {}) or {}
            self._logfh = open(self._p("数据", "run.log"), "a", encoding="utf-8")
            self.log(f"===== 断点续跑 {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====")
            self._probe_all()
            return
        cfg = self.cfg
        ffmpeg.ffmpeg_bin()
        self.batch_dir = self._make_batch_dir()
        self._logfh = open(self._p("数据", "run.log"), "a", encoding="utf-8")
        self.log(f"===== 批量视频混剪工具 v{VERSION} 任务开始 {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====")
        self.log(f"输出目录：{self.batch_dir}")
        self._probe_all()
        first = next((self.infos[p] for p in cfg.materials if self.infos.get(p) and self.infos[p].ok), None)
        self.canvas = cfg.canvas_size(first)
        self.dup = self._scan_dedup() if cfg.dedup_enabled else None
        scene_map = self._scan_scenes()
        self.stage("正在生成成片计划…")
        planner = Planner(cfg, self.infos, scene_map, log=self.log, dup=self.dup)
        self.plans, seed = planner.build()
        for w in planner.warnings:
            self.log("! " + w, level="warn")
        if not self.plans:
            raise RuntimeError("无法生成任何成片计划（素材不足）")
        durs = [p.duration for p in self.plans]
        self.log(f"计划完成：{len(self.plans)} 条，时长 {min(durs):.1f}s ~ {max(durs):.1f}s"
                 + (f"（上限 {cfg.limit_seconds():.0f}s）" if cfg.limit_seconds() > 0 else "") + f"，随机种子 {seed}")
        save_json(self._p("数据", "task.json"), {"version": VERSION, "created": datetime.datetime.now().isoformat(),
                                                 "config": cfg.to_dict(), "seed": seed})
        save_json(self._p("数据", "plan.json"), {"canvas": list(self.canvas), "seed": seed,
                                                 "items": [p.to_dict() for p in self.plans]})

    # ------------------------------------------------------------ 执行
    def _ctx(self, enc_state, threads):
        cfg = self.cfg
        W, H = self.canvas
        cmap = {}
        for k, v in (cfg.cleanup_map or {}).items():
            cmap[os.path.abspath(k)] = v
            cmap[k] = v
        return rd.RenderCtx(W=W, H=H, fps=int(cfg.fps), fit=cfg.fit, keep_audio=(cfg.keep_audio != "mute"),
                            infos=self.infos, cleanup_map=cmap, parts_dir=self._p("_tmp", "parts"),
                            enc=enc_state, threads=threads, cancel=self.cancel, log=self.log)

    def _progress(self):
        with self.lock:
            run = sum(self.item_frac.values())
            pct = (self.done + self.failed + self.skipped + run) / max(1, self.total)
            self.emit("progress", done=self.done, failed=self.failed, total=self.total, pct=min(1.0, pct))

    def _work(self, item: PlanItem, ctx, refcount):
        cfg = self.cfg
        name = item.name
        out = self._p("成片", name + ".mp4")
        if self.soft.is_set():
            return "skipped", None
        t0 = time.time()
        weight_parts = 0.55
        self.emit("item", index=item.index, name=name, status="running", stage="准备", pct=0.0)
        last = [0.0]

        def setp(f, stage):
            with self.lock:
                self.item_frac[item.index] = f
            if f - last[0] >= 0.02 or f >= 1.0:
                last[0] = f
                self.emit("item", index=item.index, name=name, status="running", stage=stage, pct=f)
                self._progress()
        job_dir = self._p("_tmp", "jobs", f"{item.index:04d}")
        files = self._render_item(item, ctx, out, job_dir, setp, weight_parts)
        n = len(files)
        extra_files = []
        if cfg.dedup_postcheck:
            setp(weight_parts + (1 - weight_parts) * 0.92, "成片复查")
            try:
                if cfg.mode == "whole":
                    drop = self._whole_dups(item, out, ctx)
                    if drop:
                        names = "、".join(os.path.basename(item.segments[k]["src"]) for k in drop)
                        self._drop_segments(item, drop, ctx.fps)
                        self._plan_changed = True
                        self.log(f"成片复查：{name} 中素材「{names}」与前面的素材有重复画面，已整段移除并重新合成"
                                 f"（整段拼接不拆素材），时长 {item.duration:.1f}s")
                        extra_files = [f for f in self._render_item(item, ctx, out, job_dir, setp, weight_parts)
                                       if f not in files]
                else:
                    self._postcheck(item, out, ctx)
            except ffmpeg.Cancelled:
                raise
            except Exception as e:  # 复查失败不影响成片
                self.log(f"! 成片复查失败（{name}）：{e}", level="warn")
        cover = None
        if cfg.cover:
            try:
                # 成片里全程已有标题时，封面不再重复叠加标题
                title_in_video = bool(item.title) and float(cfg.title_duration or 0) <= 0
                cover_title = item.title if (cfg.cover_title and not title_in_video) else ""
                cover = rd.make_cover(out, item.duration, self._p("封面", name + ".jpg"), title=cover_title,
                                      title_style=cfg.title_style or None,
                                      min_t=float(cfg.title_duration or 0) if cover_title else 0.0)
            except Exception as e:  # 封面失败不影响成片
                self.log(f"封面生成失败（{name}）：{e}", level="warn")
        elapsed = time.time() - t0
        # 清理：不再被其他成片使用的片段
        if not cfg.keep_temp:
            shutil.rmtree(job_dir, ignore_errors=True)
            with self.lock:
                for f in files:
                    refcount[f] = refcount.get(f, 1) - 1
                    if refcount[f] <= 0:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                for f in extra_files:   # 复查重新合成时新增、别的成片用不到的片段
                    if f not in refcount:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
        return "ok", {"file": out, "cover": cover, "elapsed": round(elapsed, 1), "parts": n}

    def run(self):
        t_start = time.time()
        stopped = False
        utils.prevent_sleep(True)
        try:
            try:
                self.prepare()
            except Exception as e:
                self.log(f"× 任务准备失败：{e}", level="error")
                self.emit("finished", ok=0, failed=0, skipped=0, total=0, dir=self.batch_dir or "",
                          elapsed=time.time() - t_start, stopped=False, error=str(e))
                return {"ok": 0, "failed": 0, "error": str(e)}
            cfg = self.cfg
            W, H = self.canvas
            enc = rd.EncoderState(cfg.encoder, log=self.log)
            conc = int(cfg.concurrency or 0) or utils.recommend_concurrency(W, H, gpu=enc.is_gpu)
            npar = utils.recommend_part_workers(W, H, conc)
            threads = utils.threads_per_job(npar)
            self.part_pool = ThreadPoolExecutor(max_workers=npar)
            mode = ffmpeg.PERF_MODES.get(ffmpeg.perf_mode(), {})
            self.log(f"画布 {W}×{H} @{cfg.fps}fps，编码器：{enc.enc}，并发 {conc} 条成片，片段并行 {npar} 路"
                     f"（每路 {threads} 线程），电脑负载：{mode.get('label', '')}"
                     + (f"（CPU 最多 {int(mode.get('cpu', 1) * 100)}%）" if mode.get("cpu", 1) < 1 else ""))
            # 磁盘空间预估
            est_gb = sum(p.duration for p in self.plans) * 1.3 / 1024 + 1.0
            free = utils.free_disk_gb(self.batch_dir)
            if free < est_gb:
                self.log(f"! 磁盘剩余 {free:.1f}GB，预计需要约 {est_gb:.1f}GB，可能不足", level="warn")
            ctx = self._ctx(enc, threads)
            ctx.final_threads = utils.final_threads(conc)
            todo = []
            for it in self.plans:
                m = self.manifest.get(it.name)
                if m and m.get("status") == "ok" and os.path.isfile(self._p("成片", it.name + ".mp4")):
                    self.done += 1
                    continue
                todo.append(it)
            self.total = len(self.plans)
            if self.done:
                self.log(f"已完成 {self.done} 条，继续剩余 {len(todo)} 条")
            # 片段引用计数
            refcount = {}
            for it in todo:
                for spec in rd.item_parts(it, ctx):
                    f = os.path.join(ctx.parts_dir, rd.part_key(spec, ctx) + ".mov")
                    refcount[f] = refcount.get(f, 0) + 1
            self.stage(f"开始生成：共 {len(todo)} 条")
            self._progress()

            def task(it):
                last_err = None
                for attempt in range(2):
                    try:
                        st, info = self._work(it, ctx, refcount)
                        return it, st, info, None
                    except ffmpeg.Cancelled:
                        return it, "cancelled", None, None
                    except Exception as e:
                        last_err = e
                        cat = getattr(e, "category", "其他")
                        self.log(f"× 第 {it.index} 条失败（{cat}）：{e}", level="warn")
                        if getattr(e, "stderr", ""):
                            self.log("ffmpeg 输出：\n" + e.stderr[-3000:], level="debug")
                        else:
                            self.log(traceback.format_exc(), level="debug")
                        if self.soft.is_set() or cat in ("磁盘空间不足", "环境"):
                            break
                        self.log(f"重试：第 {it.index} 条自动重试…")
                return it, "failed", None, last_err

            with ThreadPoolExecutor(max_workers=conc) as ex:
                futs = [ex.submit(task, it) for it in todo]
                for f in as_completed(futs):
                    it, st, info, err = f.result()
                    with self.lock:
                        self.item_frac.pop(it.index, None)
                    if st == "ok":
                        self.done += 1
                        self.manifest[it.name] = {"status": "ok", "index": it.index, "duration": it.duration,
                                                  "elapsed": info["elapsed"], "time": datetime.datetime.now().isoformat()}
                        self.results[it.index] = ("ok", info, None)
                        self._save_manifest()
                        self.log(f"√ [{self.done}/{self.total}] {it.name}.mp4  时长 {it.duration:.1f}s  "
                                 f"用时 {info['elapsed']:.0f}s")
                        self.emit("item", index=it.index, name=it.name, status="ok", stage="完成", pct=1.0,
                                  file=info["file"])
                    elif st == "failed":
                        self.failed += 1
                        cat = getattr(err, "category", "其他")
                        self.manifest[it.name] = {"status": "failed", "index": it.index, "error": str(err)[:300],
                                                  "category": cat}
                        self.results[it.index] = ("failed", None, f"{cat}：{err}")
                        self._save_manifest()
                        self.emit("item", index=it.index, name=it.name, status="failed", stage=cat, pct=0.0,
                                  error=str(err)[:300])
                    else:
                        self.skipped += 1
                        stopped = True
                        self.results[it.index] = ("skipped", None, "已停止")
                        self.emit("item", index=it.index, name=it.name, status="skipped", stage="已停止", pct=0.0)
                    self._progress()
            if self.soft.is_set() or self.cancel.is_set():
                stopped = True
            # 清理临时目录
            if not self.cfg.keep_temp and not stopped and self.failed == 0:
                shutil.rmtree(self._p("_tmp"), ignore_errors=True)
            self._write_report()
            if self._plan_changed:   # 复查整段移除过素材：计划文件同步成实际成片
                plan = load_json(self._p("数据", "plan.json"), {}) or {}
                plan["items"] = [p.to_dict() for p in self.plans]
                save_json(self._p("数据", "plan.json"), plan)
            elapsed = time.time() - t_start
            msg = f"任务结束：成功 {self.done} 条，失败 {self.failed} 条" + \
                  (f"，未完成 {self.skipped} 条（可在“任务记录”中继续）" if self.skipped else "") + \
                  f"，总用时 {utils.fmt_secs(elapsed)}"
            self.log(("■ " if stopped else "★ ") + msg)
            if self.record_history:
                add_history({"dir": self.batch_dir, "name": self.cfg.name,
                             "time": datetime.datetime.now().isoformat(timespec="seconds"), "total": self.total,
                             "ok": self.done, "failed": self.failed,
                             "status": "stopped" if stopped else ("partial" if self.failed else "done")})
            self.emit("finished", ok=self.done, failed=self.failed, skipped=self.skipped, total=self.total,
                      dir=self.batch_dir, elapsed=elapsed, stopped=stopped)
            return {"ok": self.done, "failed": self.failed, "skipped": self.skipped, "dir": self.batch_dir}
        finally:
            pool = getattr(self, "part_pool", None)
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            utils.prevent_sleep(False)
            with self.lock:
                if self._logfh:
                    try:
                        self._logfh.close()
                    except Exception:
                        pass
                    self._logfh = None

    def _write_report(self):
        rows = []
        for it in self.plans:
            st = self.manifest.get(it.name, {})
            status = {"ok": "成功", "failed": "失败"}.get(st.get("status"), "未完成")
            srcs = []
            for s in it.seg_objs():
                nm = os.path.basename(s.src)
                if nm not in srcs:
                    srcs.append(nm)
            rows.append([it.index, it.name + ".mp4", status, f"{it.duration:.2f}", len(it.segments),
                         "；".join(srcs), it.filter_name, os.path.basename(it.bgm) if it.bgm else "",
                         it.title, st.get("elapsed", ""), st.get("error", "")])
        path = self._p("数据", "report.csv")
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["序号", "文件名", "状态", "时长(秒)", "片段数", "使用素材", "滤镜", "背景音乐", "标题",
                            "用时(秒)", "失败原因"])
                w.writerows(rows)
        except OSError as e:
            self.log(f"报告写入失败：{e}", level="warn")
