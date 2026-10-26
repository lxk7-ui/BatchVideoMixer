"""成片计划：整段拼接 / 片段混剪。

时长规则（按需求）：只保证成片“不超过上限”，不做精确卡点。
每条成片先在 [上限×(1-浮动), 上限] 内随机一个目标值，再用片段/整段素材填充，
永远不会超过目标值（也就不会超过上限）；最后一段按剩余时间自然截短。
"""
import datetime
import math
import os
import random
import re
from dataclasses import dataclass, field, asdict
from typing import List

from . import filters_lib as fl
from .models import MixConfig

MIN_BODY_FRAMES = 3


@dataclass
class Seg:
    src: str
    start: float = 0.0      # 源素材起点（秒）
    frames: int = 0         # 输出帧数
    speed: float = 1.0
    kind: str = "clip"      # clip / image / opening / ending
    effect: str = ""        # 硬切边界的入场特效

    def out_dur(self, fps):
        return self.frames / float(fps)

    def src_dur(self, fps):
        return self.frames / float(fps) * self.speed


@dataclass
class PlanItem:
    index: int
    name: str
    segments: List[dict] = field(default_factory=list)
    transitions: List[str] = field(default_factory=list)   # 每个边界：xfade 类型或 ""
    trans_frames: int = 0
    trans_hold: bool = False    # True：整段拼接的定格过渡，相邻素材不重叠，两段之间多出转场时长
    seed: int = 0
    filter_name: str = ""
    bgm: str = ""
    bgm_offset: float = 0.0
    title: str = ""
    badge: str = ""
    variant: int = 0
    duration: float = 0.0
    fps: int = 30

    def seg_objs(self):
        return [Seg(**s) for s in self.segments]

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in names})


def total_frames(segs, transitions, trans_frames, hold=False):
    """成片总帧数：叠化转场时相邻片段重叠（减去转场帧），定格过渡时两段之间多出转场帧。"""
    n = sum(s.frames for s in segs)
    k = sum(1 for t in transitions if t)
    return n + trans_frames * k if hold else n - trans_frames * k


# 整段拼接不用“切换特效”（会盖住下一个素材开头的画面），换成效果相近的定格过渡
EFFECT_TO_XFADE = {"flash_white": "fadewhite", "flash_black": "fadeblack", "zoom_punch": "zoomin",
                   "shake": "fade", "blur_in": "hblur"}


_bad_chars = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s, maxlen=80):
    s = _bad_chars.sub("_", str(s)).strip(" ._")
    return (s or "成片")[:maxlen]


class Planner:
    def __init__(self, cfg: MixConfig, infos: dict, scene_map: dict = None, log=None, dup=None):
        self.cfg = cfg
        self._lo_frames = 0     # 时长下限（帧），build() 里按上限和浮动计算
        self.infos = infos
        self.scene_map = scene_map or {}
        self.log = log or (lambda *a, **k: None)
        self.fps = int(cfg.fps or 30)
        self.warnings = []
        self.dup = dup        # 查重索引（DupIndex），None 表示不查重
        self.pool = [infos[p] for p in cfg.materials if p in infos and infos[p].ok and infos[p].kind in ("video", "image")]
        if dup is not None and dup.full_dups:
            drop = {p for p, _q, _r in dup.full_dups}
            kept = [m for m in self.pool if m.path not in drop]
            if kept:
                for p, q, r in dup.full_dups:
                    if any(m.path == p for m in self.pool):
                        self.warnings.append(f"素材「{os.path.basename(p)}」与「{os.path.basename(q)}」"
                                             f"{r:.0%} 的画面相同，已当作重复素材剔除")
                self.pool = kept
        self.openings = [infos[p] for p in cfg.openings if p in infos and infos[p].ok and infos[p].kind in ("video", "image")]
        self.endings = [infos[p] for p in cfg.endings if p in infos and infos[p].ok and infos[p].kind in ("video", "image")]
        self.bgms = [infos[p] for p in cfg.bgms if p in infos and infos[p].ok and infos[p].duration > 1]
        # 使用统计（跨成片，均衡素材覆盖）
        self.bin = 0.5
        self.usage = {}
        for m in self.pool:
            nb = max(1, int(math.ceil(max(m.duration, 0.5) / self.bin)))
            self.usage[m.path] = [0.0] * nb
        self.use_count = {m.path: 0 for m in self.pool}
        self.fingerprints = []
        self.sequences = set()
        tt = cfg.transition_types or []
        self.xfade_types = [t for t in tt if t in fl.XFADE_TYPES] or list(fl.DEFAULT_XFADE)
        self.effect_types = [t for t in tt if t in fl.CUT_EFFECTS] or list(fl.DEFAULT_CUT_EFFECTS)
        self.trans_frames = 0
        if cfg.transition_mode in ("xfade", "mixed"):
            self.trans_frames = max(2, int(round(float(cfg.transition_dur or 0.5) * self.fps)))

    # ------------------------------------------------------------ 工具
    def _usable_range(self, m):
        """去掉掐头去尾后的可用区间。"""
        head = max(0.0, float(self.cfg.trim_head or 0))
        tail = max(0.0, float(self.cfg.trim_tail or 0))
        a, b = head, max(0.0, m.duration - tail - 0.05)
        if b - a < 1.0:  # 素材太短则忽略掐头去尾
            a, b = 0.0, max(0.0, m.duration - 0.05)
        return a, b

    def _image_frames(self, rng):
        base = max(0.8, float(self.cfg.image_duration or 3.0))
        return max(int(self.fps * 0.8), int(round(base * rng.uniform(0.85, 1.15) * self.fps)))

    def _clip_frames(self, m, rng):
        if m.kind == "image":
            return self._image_frames(rng)
        a, b = self._usable_range(m)
        return max(1, int(math.floor((b - a) * self.fps)) - 1)

    def _choose_transition(self, rng):
        mode = self.cfg.transition_mode
        if mode == "none" or not mode:
            return None
        if rng.random() * 100 >= float(self.cfg.transition_prob or 0):
            return None
        if mode == "xfade":
            return ("xfade", rng.choice(self.xfade_types))
        if mode == "effect":
            return ("effect", rng.choice(self.effect_types))
        if rng.random() < 0.5:
            return ("xfade", rng.choice(self.xfade_types))
        return ("effect", rng.choice(self.effect_types))

    def _pick_material(self, rng, prev, item_count, exhausted):
        weights = []
        for m in self.pool:
            if m.path in exhausted:
                weights.append(0.0)
                continue
            if len(self.pool) > 1 and m.path == prev:
                weights.append(0.0)
                continue
            dur = m.duration if m.kind == "video" else float(self.cfg.image_duration or 3)
            used = sum(self.usage.get(m.path, [0])) * self.bin
            w = math.sqrt(max(dur, 0.5)) / (1.0 + used / max(dur, 1.0))
            w *= 0.3 ** item_count.get(m.path, 0)
            weights.append(w)
        if sum(weights) <= 0:
            cands = [m for m in self.pool if m.path not in exhausted]
            if not cands:
                return None
            return rng.choice(cands)
        return rng.choices(self.pool, weights=weights, k=1)[0]

    def _coverage_cost(self, m, start, length):
        arr = self.usage.get(m.path)
        if not arr:
            return 0.0
        a = int(start / self.bin)
        b = max(a + 1, int(math.ceil((start + length) / self.bin)))
        seg = arr[a:b]
        return sum(seg) / max(1, len(seg))

    @staticmethod
    def _overlap(ranges, s, e):
        tot = 0.0
        for a, b in ranges:
            tot += max(0.0, min(b, e) - max(a, s))
        return tot / max(1e-6, e - s)

    def _pick_start(self, rng, m, src_len, item_ranges, item_used=None):
        a, b = self._usable_range(m)
        max_start = max(a, b - src_len)
        cands = set()
        cuts = self.scene_map.get(m.path) if self.cfg.use_scenes else None
        if cuts:
            shot_starts = [a] + [c for c in cuts if a <= c <= max_start]
            for c in rng.sample(shot_starts, min(10, len(shot_starts))):
                cands.add(round(c, 3))
        for _ in range(8):
            cands.add(round(rng.uniform(a, max_start), 3) if max_start > a else a)
        best = None
        for c in cands:
            cost = self._coverage_cost(m, c, src_len) + 6.0 * self._overlap(item_ranges, c, c + src_len) \
                + rng.random() * 0.3
            if self.dup is not None and item_used:
                cost += 10.0 * self.dup.conflict(m.path, c, c + src_len, item_used)
            if best is None or cost < best[0]:
                best = (cost, c)
        start = best[1]
        overlap = self._overlap(item_ranges, start, start + src_len)
        # 若起点在镜头切点上，尽量在下一个切点结束（不超过要求长度）
        if cuts:
            nxt = [c for c in cuts if start + 0.5 < c < start + src_len]
            if nxt and (nxt[-1] - start) >= float(self.cfg.seg_min or 1) * 0.9:
                src_len = nxt[-1] - start
        return start, src_len, overlap

    # ------------------------------------------------------------ 单条
    def _plan_segment_mode(self, rng, target_frames):
        cfg = self.cfg
        fps = self.fps
        segs, trans = [], []
        T = self.trans_frames
        smin = max(int(round(float(cfg.seg_min) * fps)), int(fps * 0.5))
        smax = max(smin, int(round(float(cfg.seg_max) * fps)))
        used = 0

        def add(seg, tr):
            nonlocal used
            if segs:
                kind = tr[0] if tr else ""
                if kind == "xfade":
                    trans.append(tr[1])
                    used += seg.frames - T
                else:
                    trans.append("")
                    if kind == "effect":
                        seg.effect = tr[1]
                    used += seg.frames
            else:
                used += seg.frames
            segs.append(seg)

        # 片头
        if self.openings:
            op = rng.choice(self.openings) if cfg.opening_random else self.openings[self._idx % len(self.openings)]
            n = min(self._clip_frames(op, rng), target_frames)
            if n > 0:
                add(Seg(op.path, self._usable_range(op)[0] if op.kind == "video" else 0.0, n, 1.0,
                        "opening" if op.kind == "video" else "image"), None)
        # 片尾预留
        end_seg = None
        if self.endings:
            ed = rng.choice(self.endings) if cfg.opening_random else self.endings[self._idx % len(self.endings)]
            n = self._clip_frames(ed, rng)
            n = min(n, max(0, target_frames - used))
            if n >= int(fps * 0.5):
                end_seg = Seg(ed.path, 0.0, n, 1.0, "ending" if ed.kind == "video" else "image")
        budget = target_frames - (end_seg.frames if end_seg else 0)

        item_count = {}
        item_ranges = {}
        exhausted = set()
        prev = None
        guard = 0
        item_used = set()
        dup_fail = {}
        if self.dup is not None:
            for sg in segs + ([end_seg] if end_seg else []):
                item_used |= self.dup.ids_in(sg.src, sg.start, sg.start + sg.src_dur(fps) if sg.kind != "image" else 0.5)
        while guard < 400:
            guard += 1
            tr = self._choose_transition(rng) if segs else None
            overlap = T if (tr and tr[0] == "xfade") else 0
            remaining = budget - used + overlap
            min_need = smin if segs else min(smin, int(fps * 0.8))
            if remaining < min_need and used < self._lo_frames:
                min_need = min(min_need, int(fps * 0.8))   # 还没到时长下限：允许补一个短一点的片段（不短于 0.8 秒）
            if remaining < min_need:
                break
            m = self._pick_material(rng, prev, item_count, exhausted)
            if m is None:
                break
            if self.dup is not None and item_count.get(m.path, 0) >= 1 and                     self.dup.static_ratio.get(m.path, 0.0) >= 0.6 and m.duration <= 15:
                exhausted.add(m.path)   # 短的静止素材（单图推拉/商品特写）同一条只用一次，否则看起来就是重复画面
                if len(exhausted) >= len(self.pool):
                    break
                continue
            want = rng.randint(smin, smax)
            want = min(want, remaining)
            if remaining - want < smin * 0.6:
                want = min(remaining, int(smax * 1.3))
            speed = 1.0
            if m.kind == "image":
                if self.dup is not None and self.dup.ids_in(m.path, 0.0, 0.5) & item_used:
                    exhausted.add(m.path)   # 同一张图（或重复图片）本条已用过
                    if len(exhausted) >= len(self.pool):
                        break
                    continue
                n = min(self._image_frames(rng), remaining)
                seg = Seg(m.path, 0.0, n, 1.0, "image")
            else:
                if cfg.speed_enabled:
                    lo, hi = sorted((float(cfg.speed_min or 1), float(cfg.speed_max or 1)))
                    speed = round(rng.uniform(max(0.5, lo), min(2.0, hi)), 3)
                a, b = self._usable_range(m)
                avail = b - a
                src_len = want / fps * speed
                if src_len > avail:
                    src_len = avail
                rngs = item_ranges.setdefault(m.path, [])
                avoid = rngs
                if self.dup is not None and self.dup.static_ratio.get(m.path, 0.0) >= 0.6:
                    # 长的静止素材（口播/固定机位）可以再用，但两次取用在素材里至少相隔 8 秒
                    avoid = [(a0 - 8.0, a1 + 8.0) for a0, a1 in rngs]
                start, src_len, ov = self._pick_start(rng, m, src_len, avoid, item_used)
                if self.dup is not None:
                    # 剪辑前查重：只保留与本条已用画面不重复的最长连续区间
                    wins = self.dup.clean_windows(m.path, start, start + src_len, item_used)
                    best = max(wins, key=lambda w: w[1] - w[0]) if wins else None
                    min_ok = max(0.8, 0.6 * float(cfg.seg_min or 1)) * speed
                    if best is None or best[1] - best[0] < min(min_ok, src_len * 0.95):
                        dup_fail[m.path] = dup_fail.get(m.path, 0) + 1
                        if dup_fail[m.path] >= 3:
                            exhausted.add(m.path)
                            if len(exhausted) >= len(self.pool):
                                break
                        continue
                    start, src_len = best[0], best[1] - best[0]
                if ov > 0.5 and not cfg.allow_repeat:
                    exhausted.add(m.path)
                    if len(exhausted) >= len(self.pool):
                        break
                    continue
                n = int(math.floor(src_len / speed * fps))
                if n < min(min_need, int(fps * 0.5)):
                    exhausted.add(m.path)
                    if len(exhausted) >= len(self.pool):
                        break
                    continue
                n = min(n, remaining)
                seg = Seg(m.path, round(start, 3), n, speed, "clip")
                rngs.append((start, start + n / fps * speed))
            # xfade 需要两侧都有足够帧；不满足则降级为硬切，并扣回重叠帧，保证不超时
            if overlap:
                prev_seg = segs[-1]
                left_ov = T if (len(trans) and trans[-1]) else 0
                if prev_seg.frames < left_ov + T + MIN_BODY_FRAMES or seg.frames < T + MIN_BODY_FRAMES + T:
                    tr = None
                    seg.frames = min(seg.frames, budget - used)
                    if seg.frames < int(fps * 0.5):
                        break
            add(seg, tr)
            if self.dup is not None:
                item_used |= self.dup.ids_in(seg.src, seg.start,
                                             seg.start + seg.src_dur(fps) if seg.kind != "image" else 0.5)
            item_count[m.path] = item_count.get(m.path, 0) + 1
            prev = m.path
        if end_seg:
            tr = self._choose_transition(rng) if segs else None
            if tr and tr[0] == "xfade":
                prev_seg = segs[-1] if segs else None
                left_ov = T if (len(trans) and trans[-1]) else 0
                if not prev_seg or prev_seg.frames < left_ov + T + MIN_BODY_FRAMES or end_seg.frames < T + MIN_BODY_FRAMES:
                    tr = None
            add(end_seg, tr)
        return segs, trans

    def _whole_transition(self, rng):
        """整段拼接的转场：一律做成定格过渡（不吃素材画面）；“切换特效”换成效果相近的过渡。"""
        tr = self._choose_transition(rng)
        if tr and tr[0] == "effect":
            return ("xfade", EFFECT_TO_XFADE.get(tr[1], "fade"))
        return tr

    def _whole_frames(self, m, rng):
        """整段拼接里一个素材的 (起点秒, 帧数)：完整时长（只扣掉用户设置的掐头去尾）。"""
        if m.kind == "image":
            return 0.0, self._image_frames(rng)
        head = max(0.0, float(self.cfg.trim_head or 0))
        tail = max(0.0, float(self.cfg.trim_tail or 0))
        if m.duration - head - tail < 1.0:
            head = tail = 0.0
        return head, max(1, int(math.floor((m.duration - head - tail) * self.fps + 1e-6)))

    def _plan_whole_mode(self, rng, target_frames):
        """整段拼接：素材一律完整使用（不拆片段），随机顺序拼接。
        - 时长上限：只挑放得下的完整素材；一个都放不下时才截取开头那一段（保留素材开头画面）。
        - 每条素材数是参考值：挑够了但成片还没到时长下限，会继续加放得下的素材。
        - 查重：素材里有和本条已用画面重复的内容时，整个素材跳过，不从中间挖掉。
        - 转场是定格过渡：不和素材重叠，素材首尾画面一帧不少，成片多出转场时长（计入上限）。"""
        cfg = self.cfg
        fps = self.fps
        T = self.trans_frames
        segs, trans = [], []
        used = 0

        def add(seg, tr):
            nonlocal used
            if segs:
                if tr and tr[0] == "xfade" and T > 0:
                    trans.append(tr[1])
                    used += T
                else:
                    trans.append("")
            used += seg.frames
            segs.append(seg)

        def frames_of(m):
            if m.kind == "image":
                return 0.0, self._image_frames(rng)
            return self._whole_frames(m, rng)

        def ids_of(src, a, b):
            return self.dup.ids_in(src, a, b) if self.dup is not None else set()

        unlimited = target_frames <= 0
        cap = target_frames if not unlimited else 10 ** 9
        lo = 0 if unlimited else min(cap, self._lo_frames)
        if self.openings:
            op = rng.choice(self.openings) if cfg.opening_random else self.openings[self._idx % len(self.openings)]
            a0, n = frames_of(op)
            n = min(n, cap)
            if n > 0:
                add(Seg(op.path, a0, n, 1.0, "opening" if op.kind == "video" else "image"), None)
        end_seg = None
        if self.endings:
            ed = rng.choice(self.endings) if cfg.opening_random else self.endings[self._idx % len(self.endings)]
            a0, n = frames_of(ed)
            n = min(n, max(0, cap - used))
            if n >= int(fps * 0.5):
                end_seg = Seg(ed.path, a0, n, 1.0, "ending" if ed.kind == "video" else "image")
        budget = cap - (end_seg.frames + T if end_seg else 0)
        item_used = set()
        for sg in segs + ([end_seg] if end_seg else []):
            item_used |= ids_of(sg.src, sg.start, sg.start + sg.src_dur(fps) if sg.kind != "image" else 0.5)
        kmin = max(1, int(cfg.clips_min or 1))
        kmax = max(kmin, int(cfg.clips_max or kmin))
        k = rng.randint(kmin, kmax)
        order = sorted(self.pool, key=lambda m: self.use_count.get(m.path, 0) + rng.random() * 0.9)
        picked = 0
        for m in order:
            if picked >= k and (unlimited or used >= lo):
                break
            a0, n = frames_of(m)
            ids = ids_of(m.path, a0, a0 + n / float(fps) if m.kind == "video" else 0.5)
            if ids & item_used:
                continue          # 与本条已用画面重复：整个素材跳过，不拆素材
            tr = self._whole_transition(rng) if segs else None
            extra = T if (tr and tr[0] == "xfade" and T > 0) else 0
            room = budget - used
            if n + extra > room:
                if extra and n <= room:
                    tr, extra = None, 0       # 放得下素材、放不下转场：这里改成硬切
                elif cfg.trim_overflow and room - extra >= int(fps * 1.0):
                    n = room - extra          # 用户允许截断：截掉这个素材的结尾凑满时长
                else:
                    continue                  # 放不下完整素材：换一个，不截断
            add(Seg(m.path, a0, n, 1.0, "clip" if m.kind == "video" else "image"), tr)
            item_used |= ids
            picked += 1
            if budget - used < int(fps * 1.0):
                break
        if picked == 0 and order:
            # 一个完整素材都放不下（素材都比上限长）：挑最短的一个，从开头截到上限，保留素材开头画面
            m = min(order, key=lambda x: frames_of(x)[1])
            a0, n = frames_of(m)
            n = min(n, max(int(fps), budget - used))
            add(Seg(m.path, a0, n, 1.0, "clip" if m.kind == "video" else "image"), None)
        if end_seg:
            tr = self._whole_transition(rng)
            if tr and used + T + end_seg.frames > cap:
                tr = None
            add(end_seg, tr)
        return segs, trans

    # ------------------------------------------------------------ 指纹/去重
    def _fingerprint(self, segs):
        fp = set()
        for s in segs:
            if s.kind in ("opening", "ending"):
                continue
            if s.kind == "image":
                fp.add((s.src, -1))
                continue
            a = int(s.start / 2.0)
            b = int((s.start + s.src_dur(self.fps)) / 2.0)
            for k in range(a, b + 1):
                fp.add((s.src, k))
        return fp

    @staticmethod
    def _jaccard(a, b):
        if not a or not b:
            return 0.0
        return len(a & b) / float(len(a | b))

    def _commit_usage(self, segs):
        for s in segs:
            if s.src in self.usage and s.kind == "clip":
                arr = self.usage[s.src]
                a = int(s.start / self.bin)
                b = int(math.ceil((s.start + s.src_dur(self.fps)) / self.bin))
                for k in range(a, min(b, len(arr))):
                    arr[k] += 1.0
            if s.src in self.use_count:
                self.use_count[s.src] += 1

    # ------------------------------------------------------------ 主入口
    def build(self, start_index=1):
        cfg = self.cfg
        if not self.pool:
            raise ValueError("没有可用的素材（请检查素材是否损坏或格式不支持）")
        fps = self.fps
        limit = cfg.limit_seconds()
        if cfg.mode == "segment" and limit <= 0:
            limit = 60.0
            self.warnings.append("片段混剪模式必须设置时长上限，已按 1 分钟处理")
        seed = int(cfg.seed or 0) or random.SystemRandom().randrange(1, 2 ** 31)
        master = random.Random(seed)
        fpct = max(0.0, min(90.0, float(cfg.limit_float or 0))) / 100.0
        self._lo_frames = int(math.ceil(limit * (1.0 - fpct) * fps)) if limit > 0 else 0
        total_footage = sum((self._usable_range(m)[1] - self._usable_range(m)[0]) if m.kind == "video"
                            else float(cfg.image_duration) for m in self.pool)
        if cfg.mode == "segment" and total_footage < limit * (1 - fpct) and not cfg.allow_repeat:
            self.warnings.append(f"素材总时长约 {total_footage:.0f}s，不足以填满 {limit:.0f}s 上限，成片会偏短")
        plans = []
        names = set()
        today = datetime.datetime.now()
        short = []
        for i in range(int(cfg.count)):
            self._idx = i
            seed_i = master.randrange(1, 2 ** 31)
            best = None
            attempts = 10 if cfg.avoid_similar else 1
            for att in range(attempts):
                rng = random.Random(seed_i + att * 7919)
                if limit > 0:
                    target = limit * rng.uniform(1.0 - fpct, 1.0)
                    target_frames = int(math.floor(target * fps))
                else:
                    target_frames = 0
                if cfg.mode == "segment":
                    segs, trans = self._plan_segment_mode(rng, target_frames)
                else:
                    segs, trans = self._plan_whole_mode(rng, target_frames)
                if not segs:
                    continue
                if cfg.mode == "whole":
                    key = tuple((s.src, s.frames) for s in segs)
                    dup = key in self.sequences
                    score = 1.0 if dup else 0.0
                else:
                    fp = self._fingerprint(segs)
                    score = max([self._jaccard(fp, o) for o in self.fingerprints] or [0.0])
                if best is None or score < best[0]:
                    best = (score, segs, trans, rng)
                if score <= (0.0 if cfg.mode == "whole" else 0.45):
                    break
            if best is None:
                self.warnings.append(f"第 {i + 1} 条无法生成计划（素材不足）")
                continue
            score, segs, trans, rng = best
            if cfg.mode == "whole":
                self.sequences.add(tuple((s.src, s.frames) for s in segs))
            else:
                self.fingerprints.append(self._fingerprint(segs))
            self._commit_usage(segs)
            hold = cfg.mode == "whole"
            nfr = total_frames(segs, trans, self.trans_frames, hold)
            dur = nfr / float(fps)
            if limit > 0 and dur < limit * (1.0 - fpct) - 0.5:
                short.append(dur)
            item = PlanItem(index=start_index + i, name="", segments=[asdict(s) for s in segs], transitions=trans,
                            trans_frames=self.trans_frames, trans_hold=hold, seed=seed_i, fps=fps,
                            duration=round(dur, 3),
                            variant=int(cfg.variant_level or 0))
            # 滤镜
            if cfg.filter_mode == "fixed" and cfg.filter_choices:
                item.filter_name = cfg.filter_choices[0]
            elif cfg.filter_mode == "random":
                choices = [c for c in (cfg.filter_choices or []) if c in fl.FILTERS] or list(fl.FILTERS.keys())
                item.filter_name = rng.choice(choices)
            # BGM
            if self.bgms:
                b = rng.choice(self.bgms)
                item.bgm = b.path
                if cfg.bgm_random_start and b.duration > dur + 6:
                    item.bgm_offset = round(rng.uniform(0, min(b.duration - dur - 2, b.duration * 0.6)), 2)
            if cfg.titles:
                item.title = rng.choice([t for t in cfg.titles if t.strip()] or [""])
            if cfg.badges and cfg.badge_enabled:
                item.badge = rng.choice(cfg.badges)
            # 命名
            first = next((s for s in segs if s.kind in ("clip", "image")), segs[0])
            stem = os.path.splitext(os.path.basename(first.src))[0]
            ctx = {"任务名": cfg.name or "混剪", "序号": f"{item.index:03d}", "日期": today.strftime("%Y%m%d"),
                   "时间": today.strftime("%H%M%S"), "素材名": stem, "时长": f"{int(round(dur))}s"}
            nm = cfg.name_template or "{任务名}_{序号}"
            for k2, v2 in ctx.items():
                nm = nm.replace("{" + k2 + "}", str(v2))
            nm = safe_name(nm)
            base, n2 = nm, 2
            while nm.lower() in names:
                nm = f"{base}_{n2}"
                n2 += 1
            names.add(nm.lower())
            item.name = nm
            plans.append(item)
        if short:
            if cfg.mode == "whole":
                why = "完整素材凑不满时长（整段拼接不截断素材）"
            else:
                why = "可用的不重复画面不足" if self.dup is not None else "素材时长不足"
            self.warnings.append(f"有 {len(short)} 条成片因{why}而短于设定下限（最短 {min(short):.1f} 秒）。"
                                 f"可增加素材" + ("，或把查重灵敏度调为“宽松”" if self.dup is not None else ""))
        return plans, seed
