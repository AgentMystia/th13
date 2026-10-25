"""th13 录像（t13r）完整解码——ReplayFile.cpp 的 python 同源孪生。

oracle 侧 --replay 用它把同一 .rpy 解出同一输入流（逐帧 u16 态/按下/释放），
与移植侧 harness replay 模式共享同一文件源。所有布局/变换语义对齐
ReplayFile.hpp 文件头引用的原版地址（0x448c40 装载、0x472280 XOR、0x458f30 LZ77）。
"""
import struct

MAGIC = b"t13r"
HEADER_SIZE = 0x24
TOP_SIZE = 0x74
STAGE_HEADER_SIZE = 0xC4
FRAME_BYTES = 6


class ReplayFormatError(Exception):
    pass


def _transform_plan(size, block):
    """0x472280/0x4723a0 公共骨架：长度/块划分（尾部门槛与奇对齐）。"""
    remainder = size % block
    tail = remainder if remainder < block // 4 else 0
    aligned = size - (size & 1)
    remaining = aligned - tail if aligned >= tail else 0
    chunks = []
    while remaining > 0:
        chunk = min(remaining, block)
        chunks.append(chunk)
        remaining -= chunk
    return chunks


def xor_decrypt(buf, init_key, step, block):
    """0x472280：顺序读 → 写块内交织反转位；密钥逐字节 +step、跨块连续。"""
    chunks = _transform_plan(len(buf), block)
    out = bytearray(buf)
    key = init_key & 0xFF
    pos = 0
    for chunk in chunks:
        base = pos
        half = (chunk + 1) // 2
        write = base + chunk - 1
        for _ in range(half):
            out[write] = buf[pos] ^ key
            key = (key + step) & 0xFF
            write -= 2
            pos += 1
        write = base + chunk
        for _ in range(chunk - half):
            write -= 2
            out[write] = buf[pos] ^ key
            key = (key + step) & 0xFF
            pos += 1
    return out


def xor_encrypt(buf, init_key, step, block):
    """0x4723a0（加密方向，单测互逆校验用）。"""
    chunks = _transform_plan(len(buf), block)
    out = bytearray(buf)
    key = init_key & 0xFF
    pos = 0
    for chunk in chunks:
        base = pos
        half = (chunk + 1) // 2
        read = base + chunk - 1
        for _ in range(half):
            out[pos] = buf[read] ^ key
            key = (key + step) & 0xFF
            read -= 2
            pos += 1
        read = base + chunk
        for _ in range(chunk - half):
            read -= 2
            out[pos] = buf[read] ^ key
            key = (key + step) & 0xFF
            pos += 1
    return out


def zun_inflate(src, limit):
    """0x458f30：MSB 位流 LZ77。字面量=1+8 位；拷贝=0+13 位地址+4 位长(=n+3)；
    地址 0 终止。环 0x2000，游标初值 1。返回 bytearray（长度必须 == limit）。"""
    out = bytearray()
    ring = bytearray(0x2000)
    ring_pos = 1
    bit = 0
    src_size = len(src)

    def read_bits(count):
        nonlocal bit
        value = 0
        for _ in range(count):
            byte = src[bit >> 3] if (bit >> 3) < src_size else 0
            value = value << 1 | ((byte >> (7 - (bit & 7))) & 1)
            bit += 1
        return value

    while True:
        if read_bits(1):
            byte = read_bits(8)
            out.append(byte)
            ring[ring_pos] = byte
            ring_pos = (ring_pos + 1) & 0x1FFF
        else:
            address = read_bits(13)
            if address == 0:
                break
            count = read_bits(4)
            for i in range(count + 3):
                byte = ring[(address + i) & 0x1FFF]
                out.append(byte)
                ring[ring_pos] = byte
                ring_pos = (ring_pos + 1) & 0x1FFF
        if len(out) > limit:
            raise ReplayFormatError("inflate overflow")
    if len(out) != limit:
        raise ReplayFormatError("inflate size mismatch %d != %d" % (len(out), limit))
    return out


class ReplayStage:
    __slots__ = ("number", "stamp", "frame_count", "blob_size", "score",
                 "input", "events", "header")

    def __init__(self):
        self.number = 0
        self.stamp = 0
        self.frame_count = 0
        self.blob_size = 0
        self.score = 0
        self.input = b""
        self.events = b""
        self.header = b""    # 完整 0xc4 stage 头原样字节（0x449070 复制 +0x14
                              # 起的 0x58 字节至 0x4be7b8，头内偏移=字段位置）

    def frame(self, index):
        """6 字节 = 态/按下/释放 三 u16（0x4480a0:41-43）。"""
        if index < self.frame_count and (index + 1) * FRAME_BYTES <= len(self.input):
            p = self.input[index * FRAME_BYTES:]
            return struct.unpack("<HHH", p[:6])
        return (0, 0, 0)

    def slow_rate(self, frame):
        tick = frame // 30
        return self.events[tick] if tick < len(self.events) else 0


class ReplayFile:
    def __init__(self):
        self.version = 0
        self.user_offset = 0
        self.comp_size = 0
        self.uncomp_size = 0
        self.player_name = ""
        self.score = 0
        self.character = 0
        self.shot_type = 0
        self.rank = 0
        self.slow_rate = 0.0
        self.practice_stage = 0
        self.stage_count = 0
        self.top_header = b""    # 解压流 [0,0x74) 顶层头原样字节（0x448c40:67
                                  # 单独 malloc 的块；0x447900 case 1 装备序
                                  # 从其 +0x5c/0x60/0x64 读出写全局——真实
                                  # 初值唯一源，禁止字段级伪造）
        self.stages = []
        self.user_blocks = []

    def decode(self, data):
        if len(data) < HEADER_SIZE:
            raise ReplayFormatError("file too small")
        if data[:4] != MAGIC:
            raise ReplayFormatError("bad magic")
        (self.version,) = struct.unpack_from("<H", data, 4)
        if self.version != 2:
            raise ReplayFormatError("unsupported replay version %d" % self.version)
        (self.user_offset, _, _, _, self.comp_size, self.uncomp_size) = struct.unpack_from(
            "<IIIIII", data, 0xC)
        if self.user_offset != self.comp_size + HEADER_SIZE:
            raise ReplayFormatError("user offset mismatch")
        if len(data) < self.comp_size + HEADER_SIZE:
            raise ReplayFormatError("truncated stage data")
        if not 0 < self.uncomp_size <= 0x4000000:
            raise ReplayFormatError("bad uncompressed size")
        body = xor_decrypt(data[HEADER_SIZE:HEADER_SIZE + self.comp_size],
                           0x5C, 0xE1, 0x400)
        body = xor_decrypt(body, 0x7D, 0x3A, 0x100)
        plain = zun_inflate(body, self.uncomp_size)

        self.top_header = bytes(plain[:TOP_SIZE])
        self.player_name = plain[:12].split(b"\0")[0].decode("ascii", "replace").rstrip()
        (self.score,) = struct.unpack_from("<i", plain, 0x14)
        (self.slow_rate,) = struct.unpack_from("<f", plain, 0x54)
        self.stage_count = plain[0x58]
        (self.character, self.shot_type, self.rank, self.practice_stage) = \
            struct.unpack_from("<iiii", plain, 0x5C)
        self.stages = []
        pos = TOP_SIZE
        for _ in range(self.stage_count):
            if pos + STAGE_HEADER_SIZE > len(plain):
                raise ReplayFormatError("stage header out of range")
            stage = ReplayStage()
            stage.header = bytes(plain[pos:pos + STAGE_HEADER_SIZE])
            stage.number, stage.stamp = struct.unpack_from("<HH", plain, pos)
            (stage.frame_count,) = struct.unpack_from("<I", plain, pos + 4)
            (stage.blob_size,) = struct.unpack_from("<I", plain, pos + 8)
            (stage.score,) = struct.unpack_from("<i", plain, pos + 0x1C)
            input_bytes = stage.frame_count * FRAME_BYTES
            if input_bytes > stage.blob_size:
                raise ReplayFormatError("stage input exceeds blob")
            if pos + STAGE_HEADER_SIZE + stage.blob_size > len(plain):
                raise ReplayFormatError("stage blob out of range")
            base = pos + STAGE_HEADER_SIZE
            stage.input = bytes(plain[base:base + input_bytes])
            stage.events = bytes(plain[base + input_bytes:base + stage.blob_size])
            if len(stage.events) != (stage.frame_count + 29) // 30:
                raise ReplayFormatError("stage event stream length mismatch")
            self.stages.append(stage)
            pos += STAGE_HEADER_SIZE + stage.blob_size

        self.user_blocks = []
        user = self.user_offset
        while user + 12 <= len(data) and data[user:user + 4] == b"USER":
            (block,) = struct.unpack_from("<I", data, user + 4)
            if block < 12 or user + block > len(data):
                raise ReplayFormatError("user block out of range")
            self.user_blocks.append(data[user + 12:user + block])
            user += block
        return self
