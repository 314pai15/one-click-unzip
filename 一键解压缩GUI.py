# -*- coding: utf-8 -*-
"""一键解压缩 - 图形界面版

复用 一键解压缩.py 和 批量删除压缩包.py 的核心逻辑，
提供目录选择、密码输入、递归选项、实时日志和删除预览确认。
"""

import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from 一键解压缩 import extract_all_archives
from 批量删除压缩包 import delete_archives


class QueueWriter:
    """把 print 输出转发到线程安全队列，由主线程刷新到日志框
    每条消息携带目标控件，日志归属由发起任务的页签决定，与用户当前所在页签无关
    """

    def __init__(self, log_queue, target_widget):
        self.queue = log_queue
        self.widget = target_widget

    def write(self, text):
        if text:
            self.queue.put((self.widget, text))

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("一键解压缩工具")
        self.root.geometry("720x520")
        self.root.minsize(620, 440)

        self.log_queue = queue.Queue()
        self.result_queue = queue.Queue()   # 后台任务的完成回调，由主线程执行
        self.running = False          # 是否有后台任务在执行
        self.preview_count = 0        # 删除页预览到的文件数
        self.preview_dir = None       # 预览时使用的目录，删除时必须一致

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_log)

    # ---------- 界面 ----------

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # ===== 页签1：批量解压 =====
        tab_extract = ttk.Frame(notebook, padding=10)
        notebook.add(tab_extract, text=" 批量解压 ")

        row1 = ttk.Frame(tab_extract)
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="目标目录：").pack(side="left")
        self.dir_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.dir_var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(row1, text="浏览...", command=self._browse_extract).pack(side="left")

        row2 = ttk.Frame(tab_extract)
        row2.pack(fill="x", pady=(0, 6))
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="递归扫描子目录",
                        variable=self.recursive_var).pack(side="left")
        ttk.Label(row2, text="      解压密码（可选）：").pack(side="left")
        self.password_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.password_var, show="*",
                  width=20).pack(side="left", padx=4)

        row3 = ttk.Frame(tab_extract)
        row3.pack(fill="x", pady=(0, 6))
        self.extract_btn = ttk.Button(row3, text="开始解压", command=self._start_extract)
        self.extract_btn.pack(side="left")
        ttk.Button(row3, text="打开目录", command=self._open_dir).pack(side="left", padx=6)
        ttk.Button(row3, text="清空日志", command=lambda: self._clear_log(self.extract_log)).pack(side="left")

        self.extract_log = self._make_log(tab_extract)
        self.extract_log.pack(fill="both", expand=True)

        # ===== 页签2：删除压缩包 =====
        tab_delete = ttk.Frame(notebook, padding=10)
        notebook.add(tab_delete, text=" 删除压缩包 ")

        drow1 = ttk.Frame(tab_delete)
        drow1.pack(fill="x", pady=(0, 6))
        ttk.Label(drow1, text="目标目录：").pack(side="left")
        self.del_dir_var = tk.StringVar()
        ttk.Entry(drow1, textvariable=self.del_dir_var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(drow1, text="浏览...", command=self._browse_delete).pack(side="left")

        drow2 = ttk.Frame(tab_delete)
        drow2.pack(fill="x", pady=(0, 6))
        self.preview_btn = ttk.Button(drow2, text="1. 预览扫描", command=self._start_preview)
        self.preview_btn.pack(side="left")
        self.delete_btn = ttk.Button(drow2, text="2. 确认删除", command=self._confirm_delete,
                                     state="disabled")
        self.delete_btn.pack(side="left", padx=6)
        ttk.Button(drow2, text="清空日志",
                   command=lambda: self._clear_log(self.delete_log)).pack(side="left")

        ttk.Label(tab_delete, foreground="#a05a00",
                  text="安全流程：先预览扫描确认列表，再点击「确认删除」。删除不可恢复，请核对目录。").pack(
            anchor="w", pady=(0, 4))

        self.delete_log = self._make_log(tab_delete)
        self.delete_log.pack(fill="both", expand=True)

        # ===== 底部状态栏 =====
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief="sunken", anchor="w", padding=(6, 2)).pack(
            fill="x", side="bottom")

    def _clear_log(self, widget):
        """清空日志：控件平时处于 disabled 状态，需先恢复才能删除内容"""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    def _make_log(self, parent):
        log = scrolledtext.ScrolledText(parent, height=12, state="disabled",
                                        font=("Consolas", 10))
        return log

    def _log(self, widget, text):
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _poll_log(self):
        """主线程每 100ms 刷新一次日志和任务结果，避免跨线程操作 tkinter 控件"""
        try:
            while True:
                widget, text = self.log_queue.get_nowait()
                self._log(widget, text)
        except queue.Empty:
            pass

        try:
            while True:
                callback, args = self.result_queue.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        except Exception as e:
            self._log(self.extract_log, f"\n[ERROR] 回调异常: {e}\n")
        finally:
            # 无论上面是否抛出异常，都保证轮询链不断裂
            self._poll_job = self.root.after(100, self._poll_log)

    # ---------- 通用 ----------

    def _busy_begin(self, msg):
        self.running = True
        self.status_var.set(msg)
        self.root.configure(cursor="watch")

    def _busy_end(self, msg):
        self.running = False
        self.status_var.set(msg)
        self.root.configure(cursor="")

    def _check_running(self):
        if self.running:
            messagebox.showwarning("请稍候", "当前有任务正在执行，请等待完成。")
            return True
        return False

    def _browse(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(os.path.normpath(path))

    def _open_dir(self):
        path = self.dir_var.get().strip()
        if path and os.path.isdir(path):
            os.startfile(path)

    def _on_close(self):
        """任务运行中关窗需确认，避免解压被硬杀留下不完整文件"""
        if self.running and not messagebox.askyesno(
                "退出确认",
                "有任务正在执行，现在关闭窗口会中断任务，可能留下不完整的解压文件。\n\n确定要退出吗？"):
            return
        # 取消待执行的轮询回调，避免销毁后仍触发 _poll_log 报错
        if getattr(self, "_poll_job", None):
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.root.destroy()

    # ---------- 解压 ----------

    def _browse_extract(self):
        self._browse(self.dir_var)

    def _start_extract(self):
        if self._check_running():
            return
        path = self.dir_var.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showerror("错误", "请先选择一个有效的目录。")
            return

        password = self.password_var.get() or None
        recursive = self.recursive_var.get()

        self._busy_begin("正在解压...")
        self.extract_btn.configure(state="disabled")
        self._log(self.extract_log, f"\n>>> 开始处理: {path}\n")

        threading.Thread(target=self._extract_worker,
                         args=(path, recursive, password), daemon=True).start()

    def _extract_worker(self, path, recursive, password):
        old_stdout = sys.stdout
        sys.stdout = QueueWriter(self.log_queue, self.extract_log)
        try:
            ok, fail = extract_all_archives(path, recursive=recursive, password=password)
            if ok + fail == 0:
                summary = "未找到任何 .zip / .rar / .7z 文件"
            else:
                summary = f"完成：成功 {ok} 个，失败 {fail} 个"
            self.result_queue.put((self._extract_done, (summary, fail)))
        except Exception as e:
            err = f"发生异常: {e}"
            self.result_queue.put((self._extract_done, (err, 1)))
        finally:
            sys.stdout = old_stdout

    def _extract_done(self, summary, fail_count):
        self._busy_end(summary)
        self._log(self.extract_log, f"\n=== {summary} ===\n")
        self.extract_btn.configure(state="normal")
        if fail_count > 0:
            messagebox.showwarning("完成", summary)
        else:
            messagebox.showinfo("完成", summary)

    # ---------- 删除压缩包 ----------

    def _browse_delete(self):
        self._browse(self.del_dir_var)

    def _start_preview(self):
        if self._check_running():
            return
        path = self.del_dir_var.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showerror("错误", "请先选择一个有效的目录。")
            return

        self._busy_begin("正在扫描...")
        self.preview_btn.configure(state="disabled")
        self.delete_btn.configure(state="disabled")
        self._log(self.delete_log, f"\n>>> 预览扫描: {path}\n")

        threading.Thread(target=self._preview_worker, args=(path,), daemon=True).start()

    def _preview_worker(self, path):
        old_stdout = sys.stdout
        sys.stdout = QueueWriter(self.log_queue, self.delete_log)
        try:
            count = delete_archives(path, dry_run=True)
            self.result_queue.put((self._preview_done, (count, path)))
        except Exception as e:
            err = f"发生异常: {e}"
            self.result_queue.put((self._preview_done, (-1, err)))
        finally:
            sys.stdout = old_stdout

    def _preview_done(self, count, path):
        if count < 0:
            # path 参数在异常路径上传的是错误信息
            self._busy_end("预览扫描失败")
            self._log(self.delete_log, f"\n{path}\n")
            self.preview_btn.configure(state="normal")
            messagebox.showerror("错误", path)
            return
        self._busy_end(f"预览完成：找到 {count} 个压缩包")
        self.preview_btn.configure(state="normal")
        if count > 0:
            self.preview_count = count
            self.preview_dir = path
            self.delete_btn.configure(state="normal")
            self._log(self.delete_log,
                      f"\n共找到 {count} 个压缩包，核对无误后点击「确认删除」。\n")
        elif count == 0:
            self._log(self.delete_log, "\n未找到任何压缩包文件。\n")

    def _confirm_delete(self):
        if self._check_running():
            return
        if not messagebox.askyesno(
                "确认删除",
                f"将永久删除 {self.preview_dir} 下的 "
                f"{self.preview_count} 个压缩包（.zip/.rar/.7z）。\n\n"
                f"删除后无法恢复，确定继续吗？"):
            return

        self._busy_begin("正在删除...")
        self.delete_btn.configure(state="disabled")
        self._log(self.delete_log, "\n>>> 正在删除...\n")

        threading.Thread(target=self._delete_worker, args=(self.preview_dir,),
                         daemon=True).start()

    def _delete_worker(self, path):
        old_stdout = sys.stdout
        sys.stdout = QueueWriter(self.log_queue, self.delete_log)
        try:
            deleted = delete_archives(path, dry_run=False)
            self.result_queue.put((self._delete_done, (deleted,)))
        except Exception as e:
            err = f"发生异常: {e}"
            self.result_queue.put((self._delete_done, (-1, err)))
        finally:
            sys.stdout = old_stdout

    def _delete_done(self, deleted, err=None):
        if err:
            self._busy_end(err)
            self._log(self.delete_log, f"\n{err}\n")
            messagebox.showerror("错误", err)
        else:
            self._busy_end(f"删除完成：共删除 {deleted} 个压缩包")
            self._log(self.delete_log, f"\n成功删除 {deleted} 个压缩包。\n")
            messagebox.showinfo("完成", f"成功删除 {deleted} 个压缩包")
        self.delete_btn.configure(state="disabled")
        self.preview_count = 0
        self.preview_dir = None


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
