"""从 Brave（Chrome 系）cookie 库离线提取 youtube.com 会话为 netscape cookies.txt。

用法（容器内）：
    python3 scripts/extract_brave_cookies.py <Cookies文件> <输出cookies.txt> [口令文件]
口令来自 KWallet「Brave Keys/Brave Safe Storage」（qdbus6 读取）；缺省回退 peanuts。
成功以 AES-GCM tag 校验为准；yt-dlp 以 latin-1 读 cookies.txt，非 Latin-1 条目跳过。
"""
import hashlib
import sqlite3
import sys

from Crypto.Cipher import AES  # pycryptodome


def derive_key(password: str, length: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1, length)


def decrypt(value: bytes, password: str) -> bytes | None:
    if value[:3] in (b"v10", b"v11"):
        key = derive_key(password, 32)
        nonce, ct_and_tag = value[3:15], value[15:]
        try:
            return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(
                ct_and_tag[:-16], ct_and_tag[-16:])
        except ValueError:
            return None  # tag 校验失败 = 口令不对
    # 旧式 CBC：IV 为 16 个空格
    key = derive_key(password, 16)
    iv = b" " * 16
    pt = AES.new(key, AES.MODE_CBC, iv).decrypt(value)
    return pt[:-pt[-1]] if pt and 0 < pt[-1] <= 16 else pt


def main(db_path: str, out_path: str, pw_path: str | None) -> None:
    password = "peanuts"
    if pw_path:
        password = open(pw_path).read().strip()
    con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    rows = con.execute(
        "SELECT host_key, name, encrypted_value, path, expires_utc, is_secure "
        "FROM cookies WHERE host_key LIKE '%youtube.com%' OR host_key LIKE '%google.com%'"
    ).fetchall()
    lines = ["# Netscape HTTP Cookie File"]
    ok = bad_key = skipped = 0
    for host, name, enc, path, expires, secure in rows:
        plain = decrypt(bytes(enc), password)
        if plain is None:
            bad_key += 1
            continue
        value = plain.decode("utf-8", "replace")
        try:
            value.encode("latin-1")
            str(name).encode("latin-1")
        except UnicodeEncodeError:
            skipped += 1
            continue
        ok += 1
        flag = "TRUE" if host.startswith(".") else "FALSE"
        exp = expires // 1_000_000 - 11_644_473_600 if expires else 0
        lines.append(f"{host}\t{flag}\t{path}\t{'TRUE' if secure else 'FALSE'}\t{int(exp)}\t{name}\t{value}")
    with open(out_path, "w", encoding="latin-1") as f:
        f.write("\n".join(lines) + "\n")
    print(f"tag 校验解密 {ok} 条（跳过非 Latin-1 {skipped}，口令不符 {bad_key}）→ {out_path}")
    if bad_key:
        print("存在 tag 校验失败：口令可能不正确。")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
