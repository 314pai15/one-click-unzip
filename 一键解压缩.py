import os
import zipfile
import sys
import shutil
import argparse
import lzma

CHUNK_SIZE = 64 * 1024  # 64KB 分块复制，避免大文件全量读进内存

try:
    import rarfile
    RAR_SUPPORT = True
    _unrar_found = False
    for unrar_path in [
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        r"D:\Program Files\WinRAR\UnRAR.exe",
        r"D:\Program Files (x86)\WinRAR\UnRAR.exe",
    ]:
        if os.path.isfile(unrar_path):
            rarfile.UNRAR_TOOL = unrar_path
            _unrar_found = True
            break
    if not _unrar_found:
        # 常见路径没找到时，让 rarfile 自己去 PATH / 环境变量里找
        _unrar_found = shutil.which("unrar") is not None
    UNRAR_FOUND = _unrar_found
except ImportError:
    RAR_SUPPORT = False

try:
    import py7zr
    SEVENZ_SUPPORT = True
except ImportError:
    SEVENZ_SUPPORT = False


def safe_target_path(extract_dir, file_name):
    """拼接目标路径并防 Zip Slip：路径逃出解压目录时返回 None"""
    target_path = os.path.realpath(os.path.join(extract_dir, file_name))
    base_dir = os.path.realpath(extract_dir)
    try:
        if os.path.commonpath([target_path, base_dir]) != base_dir:
            return None
    except ValueError:
        # 跨盘符（如绝对路径 D:\evil.txt 遇到 C:\ 解压目录）必然是逃逸
        return None
    return target_path


def write_entry(src, target_path):
    os.makedirs(os.path.dirname(target_path) or target_path, exist_ok=True)
    with open(target_path, "wb") as dst:
        shutil.copyfileobj(src, dst, CHUNK_SIZE)


def fix_gbk_name(file_name):
    """ZIP 未标记 UTF-8 时文件名按 cp437 还原为 GBK 中文名，失败返回原名"""
    try:
        return file_name.encode("cp437").decode("gbk")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return file_name


def check_existing_dir(extract_dir, archive_name):
    """目标目录已存在且非空时给出覆盖提示"""
    if os.path.isdir(extract_dir) and os.listdir(extract_dir):
        print(f"    [NOTE] {archive_name}: 目标目录已存在，重复文件将被覆盖")


def extract_zip(zip_path, extract_dir, password=None):
    with zipfile.ZipFile(zip_path, "r") as zf:
        encrypted = any(info.flag_bits & 0x1 for info in zf.infolist())
        if encrypted and not password:
            raise ValueError("压缩包已加密，请用 --password 提供密码")
        if password:
            zf.setpassword(password.encode("utf-8"))

        for info in zf.infolist():
            if info.flag_bits & 0x800:
                file_name = info.filename
            else:
                file_name = fix_gbk_name(info.filename)

            if file_name.endswith("/"):
                os.makedirs(safe_target_path(extract_dir, file_name) or extract_dir, exist_ok=True)
                continue

            target_path = safe_target_path(extract_dir, file_name)
            if target_path is None:
                print(f"    [WARN] 跳过路径异常条目: {file_name}")
                continue
            with zf.open(info) as src:
                write_entry(src, target_path)


def extract_rar(rar_path, extract_dir, password=None):
    with rarfile.RarFile(rar_path, "r") as rf:
        if password:
            rf.setpassword(password)
        if any(info.needs_password() for info in rf.infolist()) and not password:
            raise ValueError("压缩包已加密，请用 --password 提供密码")

        for info in rf.infolist():
            file_name = fix_gbk_name(info.filename)

            if file_name.endswith("\\") or file_name.endswith("/"):
                os.makedirs(safe_target_path(extract_dir, file_name) or extract_dir, exist_ok=True)
                continue

            target_path = safe_target_path(extract_dir, file_name)
            if target_path is None:
                print(f"    [WARN] 跳过路径异常条目: {file_name}")
                continue
            with rf.open(info) as src:
                write_entry(src, target_path)


def extract_7z(sevenz_path, extract_dir, password=None):
    # 7z 规范中文件名固定为 UTF-16 存储，不存在 GBK 乱码问题，直接解压即可
    try:
        with py7zr.SevenZipFile(sevenz_path, "r", password=password) as archive:
            if archive.needs_password() and not password:
                raise ValueError("压缩包已加密，请用 --password 提供密码")
            archive.extractall(path=extract_dir)
    except py7zr.exceptions.PasswordRequired:
        raise ValueError("压缩包已加密，请用 --password 提供密码")
    except py7zr.exceptions.CrcError:
        raise ValueError("密码错误或数据损坏（CRC 校验失败）")
    except lzma.LZMAError as e:
        if "Corrupt input data" in str(e) and password:
            raise ValueError("密码错误或数据损坏")
        raise


def extract_all_archives(target_dir, recursive=False, password=None):
    """返回值: 成功数, 失败数

    递归模式下采用多轮扫描：解压产生的新目录（如嵌套压缩包所在目录）
    不在 os.walk 的初始快照里，因此需要反复扫描直到不再发现新压缩包。
    """
    if not os.path.isdir(target_dir):
        print(f"目录不存在: {target_dir}")
        return 0, 0

    ok_count = 0
    fail_count = 0
    processed = set()   # 已处理过的 (目录, 文件名)，避免多轮扫描时重复处理
    hints_shown = set()  # 缺库提示只打印一次
    MAX_PASSES = 20      # 防御极端自嵌套压缩包导致的无限循环

    for _pass in range(MAX_PASSES):
        if recursive:
            walker = os.walk(target_dir)
        else:
            # 只扫描第一层：isfile 过滤掉子目录，避免 "xxx.zip" 同名文件夹被误当成压缩包
            walker = iter([(target_dir, [], [f for f in os.listdir(target_dir)
                                             if os.path.isfile(os.path.join(target_dir, f))])])

        found_new = False
        for root, dirs, files in walker:
            zip_files = [f for f in files if f.lower().endswith(".zip")
                         and (root, f) not in processed]
            rar_files = [f for f in files if f.lower().endswith(".rar")
                         and (root, f) not in processed]
            sevenz_files = [f for f in files if f.lower().endswith(".7z")
                            and (root, f) not in processed]

            if rar_files:
                if not RAR_SUPPORT:
                    if "rar_lib" not in hints_shown:
                        print("提示: 需要安装 rarfile 库才能解压 RAR 文件")
                        print("      pip install rarfile\n")
                        hints_shown.add("rar_lib")
                    rar_files = []
                elif not UNRAR_FOUND:
                    if "rar_tool" not in hints_shown:
                        print("提示: 检测到 RAR 文件但未找到 UnRAR.exe（需安装 WinRAR 或把 unrar.exe 加入 PATH）\n")
                        hints_shown.add("rar_tool")
                    rar_files = []

            if not SEVENZ_SUPPORT and sevenz_files:
                if "7z_lib" not in hints_shown:
                    print("提示: 需要安装 py7zr 库才能解压 7Z 文件")
                    print("      pip install py7zr\n")
                    hints_shown.add("7z_lib")
                sevenz_files = []

            count = len(zip_files) + len(rar_files) + len(sevenz_files)
            if count == 0:
                continue

            rel_path = os.path.relpath(root, target_dir)
            if rel_path == ".":
                print(f"扫描目录: {target_dir}")
            else:
                print(f"扫描目录: {rel_path}")
            print(f"  找到 {len(zip_files)} 个 ZIP, {len(rar_files)} 个 RAR, {len(sevenz_files)} 个 7Z\n")

            # 只为已安装的格式构建任务，未装库时不能引用其异常类（会 NameError）
            jobs = [(extract_zip, zip_files, zipfile.BadZipFile, "zip")]
            if RAR_SUPPORT:
                jobs.append((extract_rar, rar_files, rarfile.BadRarFile, "rar"))
            if SEVENZ_SUPPORT:
                jobs.append((extract_7z, sevenz_files, py7zr.Bad7zFile, "7z"))

            for func, archive_names, bad_file_exc, tag in jobs:
                for archive_name in archive_names:
                    processed.add((root, archive_name))
                    found_new = True
                    archive_path = os.path.join(root, archive_name)
                    extract_dir = os.path.join(root, os.path.splitext(archive_name)[0])
                    check_existing_dir(extract_dir, archive_name)

                    try:
                        func(archive_path, extract_dir, password)
                        print(f"    [OK] {archive_name} -> {os.path.basename(extract_dir)}/")
                        ok_count += 1
                    except bad_file_exc:
                        print(f"    [FAIL] {archive_name} - 不是有效的{tag}文件")
                        fail_count += 1
                    except Exception as e:
                        print(f"    [FAIL] {archive_name} - {e}")
                        fail_count += 1

            print()

        if not recursive or not found_new:
            break

    if ok_count + fail_count == 0:
        print("未找到任何 .zip、.rar 或 .7z 文件")
        return 0, 0

    print(f"共处理 {ok_count + fail_count} 个压缩包: 成功 {ok_count}, 失败 {fail_count}")
    return ok_count, fail_count


def main():
    parser = argparse.ArgumentParser(description="批量解压目录下的 .zip / .rar / .7z 压缩包")
    parser.add_argument("path", nargs="?", help="要解压的目录路径（不传则交互输入）")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="递归扫描子目录（默认只扫描第一层，避免解压产物里的嵌套压缩包被连环解压）")
    parser.add_argument("--password", help="加密压缩包的密码")
    args = parser.parse_args()

    path = args.path if args.path else input("请输入要解压的目录路径: ").strip()

    ok_count, fail_count = extract_all_archives(path, recursive=args.recursive, password=args.password)
    if ok_count + fail_count == 0:
        sys.exit(2)
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
