import sys
import tkinter as tk
from tkinter import filedialog
sys.stdout.reconfigure(encoding='utf-8')
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
value = filedialog.askdirectory(title='选择照片文件夹（含 manifest.json 时会检查采集完整性）', parent=root)
root.destroy()
print(value, flush=True)
