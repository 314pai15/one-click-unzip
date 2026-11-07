import os
import sys


ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}


def delete_archives(target_dir, dry_run=False):
    if not os.path.isdir(target_dir):
        print(f"目录不存在: {target_dir}")
        return 0

    deleted_count = 0
    for root, dirs, files in os.walk(target_dir):
        archive_files = [f for f in files if os.path.splitext(f)[1].lower() in ARCHIVE_EXTENSIONS]

        if not archive_files:
            continue

        rel_path = os.path.relpath(root, target_dir)
        if rel_path == ".":
            print(f"扫描目录: {target_dir}")
        else:
            print(f"扫描目录: {rel_path}")

        for archive_name in archive_files:
            archive_path = os.path.join(root, archive_name)
            if dry_run:
                print(f"  [预览] {archive_path}")
            else:
                try:
                    os.remove(archive_path)
                    print(f"  [已删除] {archive_path}")
                except Exception as e:
                    print(f"  [失败] {archive_path} - {e}")
            deleted_count += 1

        print()

    return deleted_count


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = input("请输入要清理压缩包的目录路径: ").strip()

    print("\n将递归删除以下类型的文件:", ", ".join(ARCHIVE_EXTENSIONS))
    print(f"目标目录: {path}\n")

    print("=== 预览模式（不会实际删除）===\n")
    count = delete_archives(path, dry_run=True)

    if count == 0:
        print("未找到任何压缩包文件")
        return

    print(f"共找到 {count} 个压缩包\n")

    confirm = input("确认删除以上文件? (y/n): ").strip().lower()
    if confirm == "y":
        print("\n=== 正在删除 ===\n")
        deleted = delete_archives(path, dry_run=False)
        print(f"成功删除 {deleted} 个压缩包")
    else:
        print("已取消操作")


if __name__ == "__main__":
    main()
