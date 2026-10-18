"""画面查重：识别素材之间 / 素材内部重复出现的画面（剪辑拼接过的素材很常见），保证每条成片里不出现重复片段。

原理
  1. 每 0.5 秒抽一帧，自动裁掉黑边/模糊背景等“无内容边框”，计算 64 位感知哈希（pHash，同时计算镜像哈希，
     镜像翻转过的重复画面也能认出）。对转码、缩放、调色、加字幕/水印等都不敏感。
  2. 找出“连续多帧都相同”的画面段（排除纯色帧；同一素材内的静止画面不算重复），
     把同一画面的所有出现位置归为同一个“内容编号”。
  3. 规划成片时，同一条成片里每个内容编号只允许出现一次（剪辑前查重）；
     生成后再抽帧复查一遍，发现重复把后出现的那段剪掉（剪辑后查重）。
"""
import hashlib
import math
import os
import threading
from collections import defaultdict

import numpy as np

from . import ffmpeg, paths

SAMPLE_FPS = 2.0
BIN = 1.0 / SAMPLE_FPS
# T：64 位指纹允许的最大差异位数（实测真重复含镜像/转码/模糊背景版 ≤12，不相关画面 ≥18）；
# min_run：至少连续多少个采样点（0.5 秒一个）都相同才算重复，防止偶然相似误判
LEVELS = {
    "loose": {"T": 7, "min_run": 4, "label": "宽松（只处理明显的重复）"},
    "standard": {"T": 10, "min_run": 3, "label": "标准（推荐）"},
    "strict": {"T": 12, "min_run": 2, "label": "严格（很相似也算重复）"},
}
_VERSION = 10
_lock = threading.Lock()
_mem = {}


# ----------------------------------------------------------------------------
# 感知哈希
# ----------------------------------------------------------------------------
def _dct_matrix(n=32):
    k = np.arange(n)[:, None]
    x = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * x + 1) * k / (2 * n)) * math.sqrt(2.0 / n)
    m[0, :] = math.sqrt(1.0 / n)
    return m.astype(np.float32)


_D = _dct_matrix()
_BITS = np.left_shift(np.uint64(1), np.arange(64, dtype=np.uint64))


def phash(frames32):
    """(n,32,32) float32 → (n,) uint64 感知哈希。"""
    if len(frames32) == 0:
        return np.zeros(0, dtype=np.uint64)
    c = np.einsum("ij,njk,lk->nil", _D, frames32, _D, optimize=True)
    low = c[:, :8, :8].reshape(len(frames32), 64)
    med = np.median(low[:, 1:], axis=1)
    bits = (low > med[:, None]).astype(np.uint64)
    return (bits * _BITS).sum(axis=1, dtype=np.uint64)


def hamming(a, b):
    return np.bitwise_count(np.bitwise_xor(a, b))


def _box_1d(energy):
    """一维“细节能量”曲线 → 两端对称的“无内容带”裁切范围 (起, 止)。"""
    L = len(energy)
    core = float(np.percentile(energy, 75)) + 1e-3
    thr = max(0.35, 0.15 * core)
    flat = energy < thr
    t = 0
    while t < L and flat[t]:
        t += 1
    b = 0
    while b < L and flat[L - 1 - b]:
        b += 1
    ok = t >= 0.03 * L and b >= 0.03 * L and abs(t - b) <= 0.35 * max(t, b) and (L - t - b) >= 0.2 * L
    return (t, L - b) if ok else (0, L)


def _content_box_frame(f):
    """单帧检测黑边 / 模糊背景填充，返回 (y0, y1, x0, x1)。
    用二阶差分（细节/锐度）区分：黑边和模糊背景几乎没有细节，真实画面细节丰富，边界更准。
    剪辑拼接素材每段画幅可能不同，所以逐帧判断；先定上下，再在内容区里定左右。最后再向内收 5%，减少边缘误差影响。"""
    ff = f.astype(np.float32)
    if ff.shape[0] < 8 or ff.shape[1] < 8:
        return 0, ff.shape[0], 0, ff.shape[1]
    row_hf = np.abs(ff[:, :-2] - 2 * ff[:, 1:-1] + ff[:, 2:]).mean(axis=1)
    y0, y1 = _box_1d(row_hf)
    band = ff[y0:y1]
    col_hf = np.abs(band[:-2] - 2 * band[1:-1] + band[2:]).mean(axis=0) if len(band) > 3 else np.ones(ff.shape[1])
    x0, x1 = _box_1d(col_hf)
    dy = int((y1 - y0) * 0.05)
    dx = int((x1 - x0) * 0.05)
    return y0 + dy, y1 - dy, x0 + dx, x1 - dx


def _hash_frames(frames):
    """原始灰度帧 → (哈希, 镜像哈希, 低信息标记)。"""
    from PIL import Image
    if len(frames) == 0:
        z = np.zeros(0, dtype=np.uint64)
        return z, z, np.zeros(0, dtype=bool)
    small = []
    for f in frames:
        y0, y1, x0, x1 = _content_box_frame(f)
        small.append(np.asarray(Image.fromarray(f[y0:y1, x0:x1]).resize((32, 32), Image.BOX), dtype=np.float32))
    small = np.stack(small)
    low = (small.std(axis=(1, 2)) < 5.0) | (small.mean(axis=(1, 2)) < 10.0)
    h = phash(small)
    hf = phash(small[:, :, ::-1].copy())
    return h, hf, low


# ----------------------------------------------------------------------------
# 素材分析（抽帧 + 哈希，可顺带镜头切点），结果按文件签名缓存
# ----------------------------------------------------------------------------
def _cache_path(info):
    key = hashlib.md5(f"{info.path}|{info.sig}|v{_VERSION}".encode("utf-8")).hexdigest()[:20]
    return os.path.join(paths.cache_dir("dedup"), key + ".npz")


def analyze_media(info, need_scenes=False, use_cache=True):
    """返回 {"h","hf","low","n"}；need_scenes=True 时同一遍解码顺带做镜头切点检测并写入镜头缓存。"""
    from . import scenes
    cp = _cache_path(info)
    if use_cache:
        with _lock:
            if cp in _mem:
                return _mem[cp]
        if os.path.isfile(cp) and not (need_scenes and not os.path.isfile(scenes._cache_file(info))):
            try:
                z = np.load(cp)
                res = {"h": z["h"], "hf": z["hf"], "low": z["low"], "n": int(len(z["h"]))}
                with _lock:
                    _mem[cp] = res
                return res
            except Exception:
                pass
    W = 128
    H = max(2, int(round(info.height * W / max(1, info.width))) // 2 * 2) if info.width else 72
    base = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin"]
    if info.kind == "image":
        args = base + ["-loglevel", "error", "-i", info.path, "-frames:v", "1", "-vf",
                       f"scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"]
        need_scenes = False
    elif need_scenes and info.duration >= 3:
        sfps = 10 if info.duration <= 600 else 5
        args = base + ["-i", info.path, "-filter_complex",
                       f"[0:v]split=2[a][b];[a]fps={sfps},scale=192:-2,scdet=threshold=10[sc];"
                       f"[b]fps={SAMPLE_FPS:g},scale={W}:{H}:flags=area,format=gray[hh]",
                       "-map", "[hh]", "-f", "rawvideo", "pipe:1", "-map", "[sc]", "-f", "null", "-"]
    else:
        need_scenes = False
        args = base + ["-loglevel", "error", "-i", info.path, "-map", "0:v:0", "-an", "-vf",
                       f"fps={SAMPLE_FPS:g},scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"]
    p = ffmpeg.run_capture(args, timeout=max(180, int(info.duration * 3)), text=False)
    raw = p.stdout or b""
    n = len(raw) // (W * H)
    frames = np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W) if n else np.zeros((0, H, W), np.uint8)
    h, hf, low = _hash_frames(frames)
    res = {"h": h, "hf": hf, "low": low, "n": int(len(h))}
    if need_scenes:
        import json
        import re
        text = (p.stderr or b"").decode("utf-8", errors="replace")
        cuts = []
        for m in re.finditer(r"lavfi\.scd\.time:\s*([0-9.]+)", text):
            t = float(m.group(1))
            if 0.3 < t < info.duration - 0.3 and (not cuts or t - cuts[-1] > 0.4):
                cuts.append(round(t, 3))
        try:
            with open(scenes._cache_file(info), "w", encoding="utf-8") as fh:
                json.dump({"path": info.path, "cuts": cuts}, fh)
        except OSError:
            pass
    if use_cache:
        try:
            np.savez(cp, h=h, hf=hf, low=low)
        except OSError:
            pass
        with _lock:
            _mem[cp] = res
    return res


# ----------------------------------------------------------------------------
# 查重索引
# ----------------------------------------------------------------------------
class DupIndex:
    """每个素材每 0.5 秒一个“内容编号”：-1 表示没有重复；>=0 表示该画面在别处也出现过（同号即同一画面）。"""

    def __init__(self, level="standard"):
        self.level = level
        self.ids = {}            # path -> np.int32[n]
        self.runs = []           # [(pathA, a0, a1, pathB, b0, b1)] 重复画面段（秒）
        self.ratio = {}          # path -> 重复画面占比
        self.full_dups = []      # [(dup_path, of_path, ratio)] 几乎整段重复的素材
        self.static_ratio = {}   # path -> 静止画面占比（单张图推拉、固定机位等）

    def _slice(self, path, s, e):
        arr = self.ids.get(path)
        if arr is None or len(arr) == 0:
            return None, 0
        a = max(0, int(math.floor(s / BIN + 1e-6)))
        b = min(len(arr), max(a + 1, int(math.ceil(e / BIN - 1e-6))))
        return arr[a:b], a

    def ids_in(self, path, s, e):
        arr, _ = self._slice(path, s, e)
        if arr is None:
            return set()
        return {int(x) for x in arr if x >= 0}

    def conflict(self, path, s, e, used):
        arr, _ = self._slice(path, s, e)
        if arr is None or not used or len(arr) == 0:
            return 0.0
        return sum(1 for x in arr if x >= 0 and int(x) in used) / float(len(arr))

    def clean_windows(self, path, s, e, used):
        """[s,e) 中不含已用内容、且自身不重复的连续区间列表（秒）。"""
        arr, a0 = self._slice(path, s, e)
        if arr is None:
            return [(s, e)]
        local = set(used or ())
        wins = []
        cur = None
        last = None
        for k, x in enumerate(arr.tolist()):
            bad = x >= 0 and x in local and x != last
            if bad:
                if cur is not None:
                    wins.append((cur, k))
                cur = None
                last = None
            else:
                if cur is None:
                    cur = k
                if x >= 0:
                    local.add(x)
                last = x
        if cur is not None:
            wins.append((cur, len(arr)))
        out = []
        for k0, k1 in wins:
            ws = max(s, (a0 + k0) * BIN)
            we = min(e, (a0 + k1) * BIN)
            if we - ws > 0.05:
                out.append((ws, we))
        return out

    def summary(self):
        n_mat = sum(1 for p, r in self.ratio.items() if r > 0)
        total = sum(min(a1 - a0, b1 - b0) for _, a0, a1, _, b0, b1 in self.runs)
        return {"runs": len(self.runs), "materials": n_mat, "seconds": round(total, 1), "full": len(self.full_dups)}


STATIC_T = 6   # 静止画面判定用更严的门槛：只有几乎不动的画面才算（缓推/缓移镜头不算）


def _self_static(h, T=STATIC_T):
    """同一素材内的静止画面：与前后 2 秒内多数帧都几乎相同。"""
    T = min(T, STATIC_T)
    n = len(h)
    cnt = np.zeros(n, dtype=np.int32)
    for off in range(1, 5):
        if off >= n:
            break
        d = hamming(h[off:], h[:-off]) <= T
        cnt[off:] += d
        cnt[:-off] += d
    return cnt >= 4


def _departs(H, a, b, T):
    """a、b 两个采样点之间是否出现过“明显不同”的画面（真重复是 A…别的画面…A，连续慢镜头则一直相似）。"""
    if b - a < 2:
        return False
    mid = H[a + 1:b]
    d = np.minimum(hamming(mid, H[a]), hamming(mid, H[b]))
    return bool((d > T + 6).any())


def find_runs(H, HF, M, TB, LOW, T, min_run, same_gap=6, static=None):
    """在全部采样中找重复画面链。返回 [(样本下标列表A, 样本下标列表B, 是否静止)]。"""
    N = len(H)
    if N < 2:
        return []
    if static is None:
        static = np.zeros(N, dtype=bool)
    valid_idx = np.nonzero(~LOW)[0]
    if len(valid_idx) < 2:
        return []
    Hv, HFv = H[valid_idx], HF[valid_idx]
    pairs = []
    blk_size = 768
    for st in range(0, len(valid_idx), blk_size):
        blk = valid_idx[st:st + blk_size]
        hb = H[blk][:, None]
        d = np.minimum(hamming(hb, Hv[None, :]), hamming(hb, HFv[None, :]))
        ii, jj = np.nonzero(d <= T)
        gi = blk[ii]
        gj = valid_idx[jj]
        keep = gj > gi
        gi, gj = gi[keep], gj[keep]
        if len(gi) == 0:
            continue
        same = M[gi] == M[gj]
        far = np.abs(TB[gi] - TB[gj]) >= same_gap
        stt = static[gi] | static[gj]
        ok = (~same) | (far & ~stt)
        for a, b, sm in zip(gi[ok].tolist(), gj[ok].tolist(), same[ok].tolist()):
            if sm and not _departs(H, a, b, T):
                continue   # 同一连续镜头缓慢变化（缓推/转台），中间没换过画面 → 不算重复
            pairs.append((a, b))
    if not pairs:
        return []
    index = {p: k for k, p in enumerate(pairs)}
    parent = list(range(len(pairs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    Ml = M.tolist()
    for k, (a, b) in enumerate(pairs):
        for da, db in ((1, 1), (1, 2), (1, 0), (2, 1), (1, 3), (2, 2)):
            pa, pb = a - da, b - db
            if pa < 0 or pb < 0 or Ml[pa] != Ml[a] or Ml[pb] != Ml[b]:
                continue
            q = index.get((pa, pb))
            if q is not None:
                ra, rb = find(k), find(q)
                if ra != rb:
                    parent[ra] = rb
    chains = defaultdict(list)
    for k in range(len(pairs)):
        chains[find(k)].append(pairs[k])
    out = []
    for plist in chains.values():
        sa = sorted({a for a, _ in plist})
        sb = sorted({b for _, b in plist})
        is_static = bool(static[sa].any() or static[sb].any())
        need = min_run + (1 if is_static else 0)
        if min(len(sa), len(sb)) >= need:
            out.append((sa, sb, is_static, plist))
    return out


def build_index(media, level="standard", log=None):
    """media: {path: {"h","hf","low","kind"}} → DupIndex。"""
    L = LEVELS.get(level, LEVELS["standard"])
    T, min_run = L["T"], L["min_run"]
    idx = DupIndex(level)
    plist = [p for p in media if media[p]["n"] > 0]
    if not plist:
        return idx
    Hs, HFs, Ms, TBs, LOWs, STs = [], [], [], [], [], []
    offsets = {}
    off = 0
    for mi, p in enumerate(plist):
        d = media[p]
        n = d["n"]
        offsets[p] = (off, n)
        Hs.append(d["h"])
        HFs.append(d["hf"])
        Ms.append(np.full(n, mi, dtype=np.int32))
        TBs.append(np.arange(n, dtype=np.int32))
        low = d["low"].copy()
        if d.get("kind") == "image":
            st = np.zeros(n, dtype=bool)
        else:
            st = _self_static(d["h"], T)
        LOWs.append(low)
        STs.append(st)
        valid_n = int((~low).sum())
        idx.static_ratio[p] = float((st & ~low).sum()) / valid_n if valid_n else 0.0
        off += n
    H = np.concatenate(Hs)
    HF = np.concatenate(HFs)
    M = np.concatenate(Ms)
    TB = np.concatenate(TBs)
    LOW = np.concatenate(LOWs)
    ST = np.concatenate(STs)
    is_img = np.concatenate([np.full(media[p]["n"], media[p].get("kind") == "image") for p in plist])
    # 图片只与图片比较（单帧），其余按视频规则
    runs = find_runs(H, HF, M, TB, LOW | is_img, T, min_run, static=ST)
    # 图片之间：单帧相同即重复
    img_idx = np.nonzero(is_img & ~LOW)[0]
    img_pairs = []
    for i in range(len(img_idx)):
        for j in range(i + 1, len(img_idx)):
            a, b = img_idx[i], img_idx[j]
            if min(hamming(H[a], H[b]), hamming(H[a], HF[b])) <= T:
                img_pairs.append((a, b))
    # 并查集：样本 → 内容编号
    parent = {}

    def f(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def u(a, b):
        ra, rb = f(a), f(b)
        if ra != rb:
            parent[ra] = rb
    for sa, sb, is_static, pl in runs:
        for a, b in pl:
            u(a, b)
        pa, pb = plist[M[sa[0]]], plist[M[sb[0]]]
        idx.runs.append((pa, round(TB[sa[0]] * BIN, 2), round((TB[sa[-1]] + 1) * BIN, 2),
                         pb, round(TB[sb[0]] * BIN, 2), round((TB[sb[-1]] + 1) * BIN, 2)))
    for a, b in img_pairs:
        u(a, b)
        idx.runs.append((plist[M[a]], 0.0, 0.0, plist[M[b]], 0.0, 0.0))
    labels = {}
    cid = 0
    all_ids = np.full(len(H), -1, dtype=np.int32)
    for x in parent:
        r = f(x)
        if r not in labels:
            labels[r] = cid
            cid += 1
        all_ids[x] = labels[r]
    for p in plist:
        o, n = offsets[p]
        idx.ids[p] = all_ids[o:o + n].copy()
        valid = ~LOW[o:o + n]
        nv = int(valid.sum())
        idx.ratio[p] = float(((all_ids[o:o + n] >= 0) & valid).sum()) / nv if nv else 0.0
    # 几乎整段重复的素材（例如同一个视频导入了两次）
    owners = defaultdict(set)
    for p in plist:
        for x in set(idx.ids[p].tolist()):
            if x >= 0:
                owners[x].add(p)
    for p in plist:
        o, n = offsets[p]
        ids = idx.ids[p]
        valid = ~LOW[o:o + n]
        nv = int(valid.sum())
        if nv < 4:
            continue
        shared = defaultdict(int)
        for x, v in zip(ids.tolist(), valid.tolist()):
            if x >= 0 and v:
                for q in owners[x]:
                    if q != p:
                        shared[q] += 1
        for q, c in shared.items():
            r = c / float(nv)
            if r >= 0.85 and plist.index(q) < plist.index(p):
                idx.full_dups.append((p, q, round(r, 3)))
                break
    idx.runs.sort(key=lambda r: -(r[2] - r[1]))
    if log:
        s = idx.summary()
        log(f"素材查重完成：发现 {s['runs']} 处重复画面（约 {s['seconds']} 秒，涉及 {s['materials']} 个素材）"
            + (f"，其中 {s['full']} 个素材几乎与其他素材完全相同" if s["full"] else ""))
    return idx


def analyze_library(infos, level="standard", need_scenes=(), cancel=None, progress=None, log=None, workers=3):
    """infos: [MediaInfo] → DupIndex。progress(done, total)。"""
    from concurrent.futures import ThreadPoolExecutor
    items = [i for i in infos if i.ok and i.kind in ("video", "image")]
    media = {}
    done = [0]
    lock = threading.Lock()

    def work(info):
        if cancel is not None and cancel.is_set():
            return info.path, None
        try:
            d = analyze_media(info, need_scenes=info.path in need_scenes)
        except Exception as e:  # 单个素材失败不影响整体
            if log:
                log(f"查重分析失败（{info.name}）：{e}", level="warn")
            d = None
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], len(items))
        return info.path, d
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for path, d in ex.map(work, items):
            if d is not None:
                dd = dict(d)
                dd["kind"] = next(i.kind for i in items if i.path == path)
                media[path] = dd
    return build_index(media, level, log=log)


# ----------------------------------------------------------------------------
# 成片复查
# ----------------------------------------------------------------------------
def check_output(info, level="standard"):
    """检查一条成片内部是否有重复画面。返回 [(a0, a1, b0, b1)]（秒），b 段为后出现的重复。"""
    L = LEVELS.get(level, LEVELS["standard"])
    # 复查门槛比素材查重低一档（至少连续 1 秒），这里只负责找“候选”：成片经过画布适配（模糊背景/黑边）、叠加贴纸标题，
    # 画面细节少时不同内容也会“看起来像”，候选由 batch.BatchRunner._real_dup 换算回素材原始画面再确认
    T, min_run = L["T"] + 2, max(2, L["min_run"] - 1)
    d = analyze_media(info, need_scenes=False, use_cache=False)
    n = d["n"]
    if n < 4:
        return []
    static = _self_static(d["h"], T)
    runs = find_runs(d["h"], d["hf"], np.zeros(n, dtype=np.int32), np.arange(n, dtype=np.int32), d["low"], T,
                     min_run, same_gap=6, static=static)
    out = []
    for sa, sb, _st, _pl in runs:
        a0, a1 = sa[0] * BIN, (sa[-1] + 1) * BIN
        b0, b1 = sb[0] * BIN, (sb[-1] + 1) * BIN
        if b0 < a0:
            a0, a1, b0, b1 = b0, b1, a0, a1
        out.append((round(a0, 2), round(a1, 2), round(b0, 2), round(b1, 2)))
    out.sort(key=lambda r: r[2])
    return out


def merge_ranges(ranges, gap=0.3):
    rs = sorted((max(0.0, a), b) for a, b in ranges if b > a)
    out = []
    for a, b in rs:
        if out and a <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
