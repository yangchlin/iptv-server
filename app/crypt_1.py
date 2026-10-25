# -*- coding: utf-8 -*-
"""3DES/DES 加密工具（电信 Authenticator 专用）

设计要点：
- 内部使用 PKCS#7 bytes 填充（原实现按字符填充，对多字节 UTF-8 会算错块长）
- 日志目录取自 config.LOG_DIR；创建失败仅打印警告，绝不因日志问题拖垮应用导入
- find_key() 仅供本地离线工具使用，服务端请勿调用（1 亿次循环会长期占用 CPU）
"""
import datetime
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait as futures_wait

from Crypto.Cipher import DES3, DES

from . import config

# ── 日志配置（尽量健壮）──
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 文件处理器：仅在成功创建时添加；失败仅警告，不影响模块导入
try:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(
        str(config.LOG_DIR / "iptv.log"), encoding="utf-8"
    )
    _file_handler.setFormatter(_formatter)
    # 避免热重载时重复添加
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        logger.addHandler(_file_handler)
except OSError as _e:
    print(f"[crypt] 无法创建日志文件，仅输出到控制台: {_e}")

# 标准输出处理器（避免重复添加）
if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
           for h in logger.handlers):
    _stdout_handler = logging.StreamHandler(sys.stdout)
    _stdout_handler.setFormatter(_formatter)
    logger.addHandler(_stdout_handler)


# ════════════════════════════════════════════════════════════════
# 填充工具（对外保留原签名，内部真正使用 bytes 版本）
# ════════════════════════════════════════════════════════════════

def _pad_bytes(data: bytes, block_size: int = 8) -> bytes:
    """PKCS#7 bytes 填充（UTF-8 编码后字节长度正确）。"""
    pad_len = block_size - len(data) % block_size
    return data + bytes([pad_len] * pad_len)


def _unpad_bytes(data: bytes) -> bytes:
    """PKCS#7 bytes 去填充。"""
    if not data:
        return data
    pad_len = data[-1]
    if 1 <= pad_len <= 8:
        return data[:-pad_len]
    return data


# ════════════════════════════════════════════════════════════════
# 加密类
# ════════════════════════════════════════════════════════════════

class PRPCrypt:
    """3DES 加密；8 位 key 自动退化为单 DES（兼容老固件）。"""

    def __init__(self, key: str):
        if len(key) == 8:
            # 8 位 key：单 DES
            self.key = key
            self.is_single_des = True
        else:
            # 补齐 / 截断到 24 字节
            self.key = key.ljust(24, "0")[:24]
            self.is_single_des = False
        self.mode = DES3.MODE_ECB

    def _make_cipher(self):
        """构造加密器（根据 key 长度自动选择 DES / 3DES）。"""
        if self.is_single_des:
            return DES.new(self.key.encode(), DES.MODE_ECB)
        return DES3.new(self.key.encode(), self.mode)

    def encrypt(self, text: str) -> str:
        """加密文本，返回 hex 字符串。"""
        padded = _pad_bytes(text.encode("utf-8"))
        cryptor = self._make_cipher()
        return cryptor.encrypt(padded).hex()

    def decrypt(self, text: str) -> str:
        """解密 hex 字符串，返回文本。"""
        try:
            cryptor = self._make_cipher()
        except Exception as e:
            logger.error(f"创建加密器失败: {e}")
            raise

        try:
            raw = bytes.fromhex(text)
            plain = cryptor.decrypt(raw)
            return _unpad_bytes(plain).decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug(f"解密失败: {e}")
            raise


# ════════════════════════════════════════════════════════════════
# 并行反推 key（8 位数字）
#
# Authenticator 的明文格式是：
#     {key}${EncryptToken}${UserID}${STBID}${ip}${mac}$$CTC
# 也就是「明文前 8 字节就是 key 本身」，所以每个候选 key 解密后先比对
# 前 8 字节，命中再看 $ 和结尾的 CTC，误报几乎为零。
#
# 单进程约 4.5 万次/秒，1 亿次要 30 多分钟，所以这里用多进程分摊。
# ════════════════════════════════════════════════════════════════

def _scan_key_chunk(task):
    """在一个区间里穷举 8 位数字 key（子进程执行，必须是模块级函数）。"""
    lo, hi, raw = task
    for x in range(lo, hi):
        k = ("%08d" % x).encode()
        try:
            plain = DES.new(k, DES.MODE_ECB).decrypt(raw)
        except Exception:
            continue
        if plain[:8] != k or b"$" not in plain:
            continue
        try:
            text = _unpad_bytes(plain).decode("utf-8", "replace")
        except Exception:
            continue
        if text.endswith("CTC") and text.count("$") >= 5:
            return k.decode()
    return None


def _default_workers() -> int:
    try:
        n = len(os.sched_getaffinity(0))      # Linux：尊重容器 CPU 限制
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    return max(1, min(4, n))


def _make_executor(workers: int):
    """反推用的执行器。

    Linux（Docker）用 fork 多进程，能吃满多核；Windows 上 spawn 会要求入口脚本
    有 __main__ 保护，服务里直接跑有风险，所以退回单线程串行（只是慢，不会崩）。
    """
    try:
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        return ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
    except (ValueError, OSError, AttributeError):
        from concurrent.futures import ThreadPoolExecutor
        logger.warning("当前平台不支持 fork，反推 key 将单进程串行执行（会慢很多）")
        return ThreadPoolExecutor(max_workers=1)


def find_key_parallel(Authenticator: str, max_iters: int = 100_000_000,
                      workers: int = 0, chunk_size: int = 250_000,
                      progress=None, should_stop=None) -> list:
    """多进程穷举 8 位数字 key。

    Args:
        Authenticator: 待解密的 hex 字符串
        max_iters: 最多试多少个（00000000 起算）
        workers: 进程数，0 = 自动（最多 4）
        chunk_size: 每个子任务的候选数量
        progress: 回调 (tested, total, elapsed_seconds)
        should_stop: 回调，返回 True 时尽快停止

    Returns:
        找到的 key 列表（通常 0 或 1 个）
    """
    try:
        raw = bytes.fromhex((Authenticator or "").strip())
    except ValueError:
        logger.error("Authenticator 不是合法的 hex 字符串")
        return []
    if not raw or len(raw) % 8:
        logger.error(f"Authenticator 长度 {len(raw)} 不是 8 的倍数，无法解密")
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
            done, _ = futures_wait(list(pending.keys()),
                                   return_when=FIRST_COMPLETED, timeout=1.0)
            for fut in done:
                size = pending.pop(fut, 0)
                tested += size
                try:
                    hit = fut.result()
                except Exception as e:
                    logger.debug(f"子进程反推失败: {e}")
                    hit = None
                if hit:
                    found.append(hit)
                if progress:
                    try:
                        progress(tested, total, time.time() - started)
                    except Exception:
                        pass
            if found:
                break
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
# 离线暴力找 key（单进程版，仅开发调试）
# ════════════════════════════════════════════════════════════════

def find_key(Authenticator: str, max_iters: int = 100_000_000) -> list:
    """尝试 00000000-99999999 范围内的 8 位数字 key 解密 Authenticator。

    Args:
        Authenticator: 待解密的 hex 字符串
        max_iters: 最大尝试次数（默认 1 亿，调用方可缩短以便快速返回）

    Returns:
        匹配成功的 key 列表（最多 20 个）
    """
    if len(Authenticator) < 10:
        logger.error("Authenticator 长度不足 10 位，无法继续")
        return []

    logger.info(
        f"开始测试 00000000-{max_iters - 1:08d} 共 {max_iters} 个 8 位数字 key，"
        f"Authenticator 长度: {len(Authenticator)}"
    )

    keys = []
    last_info = ""       # 记录最后一次成功解密的内容，供落盘
    for x in range(max_iters):
        key = f"{x:08d}"
        if x % 500_000 == 0:
            logger.info(f"已搜索至: {key}")

        pc = PRPCrypt(key)
        try:
            ee = pc.decrypt(Authenticator)
            if "$" in ee:
                keys.append(key)
                last_info = ee
                if len(keys) >= 20:
                    break
        except Exception:
            # 解密失败是常态，跳过
            continue

    # 只在找到 key 时落盘（原先无论如何都会写，且可能引用未定义的 ee/infotxt）
    if keys:
        try:
            path = config.LOG_DIR / "key.txt"
            path.write_text(
                f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"共找到 KEY：{len(keys)} 个\n"
                f"分别为：{','.join(keys)}\n"
                f"最后一次解密详情：{last_info}\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"写入 key.txt 失败: {e}")

    return keys


if __name__ == "__main__":
    # 离线调试示例：缩短 max_iters 便于快速跑完
    find_key("请输入 Authenticator hex 字符串", max_iters=10_000_000)