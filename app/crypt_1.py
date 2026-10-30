# -*- coding: utf-8 -*-
"""3DES/DES 加密工具（完美规避新版加密库退化报错）"""
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait as futures_wait

from Crypto.Cipher import DES3, DES

from . import config

# ── 日志配置 ──
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

try:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(str(config.LOG_DIR / "iptv.log"), encoding="utf-8")
    _file_handler.setFormatter(_formatter)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        logger.addHandler(_file_handler)
except OSError:
    pass

if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
    _stdout_handler = logging.StreamHandler(sys.stdout)
    _stdout_handler.setFormatter(_formatter)
    logger.addHandler(_stdout_handler)


# ════════════════════════════════════════════════════════════════
# 核心加密类（★ 修复 3DES 退化报错）
# ════════════════════════════════════════════════════════════════

class PRPCrypt:
    def __init__(self, key: str):
        if len(key) == 8:
            self.key_bytes = key.encode()
            self.is_single_des = True
        else:
            self.key_bytes = key.ljust(24, "0")[:24].encode()
            self.is_single_des = False
        self.mode = DES3.MODE_ECB

    def _make_cipher(self):
        # ★ 如果 key 是 8 位，直接走单 DES，避免新版库的 "degenerates" 拦截
        if self.is_single_des:
            return DES.new(self.key_bytes, DES.MODE_ECB)
        else:
            return DES3.new(self.key_bytes, self.mode)

    def encrypt(self, text: str) -> str:
        # 标准的 PKCS#7 填充
        pad_len = 8 - len(text.encode("utf-8")) % 8
        padded = text.encode("utf-8") + bytes([pad_len] * pad_len)
        cryptor = self._make_cipher()
        return cryptor.encrypt(padded).hex()

    def decrypt(self, text: str) -> str:
        cryptor = self._make_cipher()
        raw = bytes.fromhex(text)
        plain = cryptor.decrypt(raw)
        # 兼容原版直接替换 \x08 的粗糙做法
        return plain.replace(b"\x08", b"").decode("utf-8", errors="ignore")


# ════════════════════════════════════════════════════════════════
# 多进程扫描（直接走单 DES，极速反推）
# ════════════════════════════════════════════════════════════════

def _scan_key_chunk(task):
    lo, hi, raw = task
    for x in range(lo, hi):
        k = ("%08d" % x).encode()
        try:
            # ★ 直接使用单 DES 解密，速度最快，绝对不报退化错
            plain = DES.new(k, DES.MODE_ECB).decrypt(raw)
            
            # 必须包含电信特征
            if b"$$CTC" in plain:
                return k.decode()
        except Exception:
            pass
    return None


def _default_workers() -> int:
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    return max(1, min(4, n))


def _make_executor(workers: int):
    try:
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        return ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
    except (ValueError, OSError, AttributeError):
        from concurrent.futures import ThreadPoolExecutor
        return ThreadPoolExecutor(max_workers=1)


def find_key_parallel(Authenticator: str, max_iters: int = 100_000_000,
                      workers: int = 0, chunk_size: int = 250_000,
                      progress=None, should_stop=None) -> list:
    
    auth_clean = "".join(Authenticator.split())
    
    try:
        raw = bytes.fromhex(auth_clean)
    except ValueError:
        logger.error("Authenticator 不是合法的 hex 字符串")
        return []
    if not raw or len(raw) % 8:
        logger.error(f"Authenticator 长度 {len(raw)} 不是 8 的倍数")
        return []

    total = max(0, min(int(max_iters), 100_000_000))
    if total <= 0:
        return []
    workers = max(1, min(int(workers or _default_workers()), 8))
    chunk = max(10_000, int(chunk_size))

    started = time.time()
    tested = 0
    found = []
    pending = {}

    def submit_range(ex, lo):
        hi = min(total, lo + chunk)
        fut = ex.submit(_scan_key_chunk, (lo, hi, raw))
        pending[fut] = hi - lo
        return hi

    ex = _make_executor(workers)
    try:
        next_lo = 0
        while next_lo < total and len(pending) < workers * 2:
            next_lo = submit_range(ex, next_lo)

        while pending:
            if should_stop and should_stop():
                logger.info("反推 key 已被手动停止")
                break
            done, _ = futures_wait(list(pending.keys()), return_when=FIRST_COMPLETED, timeout=1.0)
            for fut in done:
                size = pending.pop(fut, 0)
                tested += size
                try:
                    hit = fut.result()
                except Exception:
                    hit = None
                if hit:
                    found.append(hit)
                if progress:
                    try:
                        progress(tested, total, time.time() - started)
                    except Exception:
                        pass
            if found:
                break # 找到一个立刻停
            while next_lo < total and len(pending) < workers * 2:
                next_lo = submit_range(ex, next_lo)
    finally:
        for fut in pending:
            fut.cancel()
        ex.shutdown(wait=False, cancel_futures=True)

    if found:
        logger.info(f"反推 key 成功: {found[0]}（试了 {tested} 个）")
        
    return found


# ════════════════════════════════════════════════════════════════
# 兼容原有单进程测试
# ════════════════════════════════════════════════════════════════
def find_key(Authenticator: str, max_iters: int = 100_000_000) -> list:
    return find_key_parallel(Authenticator, max_iters)


if __name__ == "__main__":
    # 离线调试示例：缩短 max_iters 便于快速跑完
    find_key("请输入 Authenticator hex 字符串", max_iters=10_000_000)