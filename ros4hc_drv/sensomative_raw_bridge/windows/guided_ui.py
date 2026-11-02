"""Local Windows cues share exactly the same monotonic origin as capture."""
import asyncio
import threading
import time
import tkinter as tk
import winsound


def run_guided(args, receiver_type):
    class State:
        ready = False
        start_ns = None
        cancel = False
        done = False
        status = 'Sensomativeへ接続しています…'
        receiver = None
    ui = State()
    def worker():
        async def run():
            receiver = receiver_type(args)
            ui.receiver = receiver
            receiver.ui = ui
            try:
                await receiver.run()
            except Exception as exc:
                ui.status = 'エラー：' + repr(exc)
                ui.done = True
        asyncio.run(run())
    thread = threading.Thread(target=worker, daemon=False)
    window = tk.Tk()
    window.title('Sensomative BLE → TCP → ROS 2：30秒荷重試験')
    window.geometry('800x370')
    window.attributes('-topmost', True)
    label = tk.Label(window, font=('Yu Gothic UI', 23), wraplength=760)
    label.pack(pady=25)
    detail = tk.Label(window, font=('Yu Gothic UI', 16))
    detail.pack(pady=10)
    def start():
        if ui.ready and ui.start_ns is None:
            ui.start_ns = time.perf_counter_ns()+5_000_000_000
    button = tk.Button(window, text='開始（5秒後に記録開始）', command=start, font=('Yu Gothic UI', 17), state='disabled')
    button.pack(pady=15)
    tk.Label(window, text='0–5 無荷重 / 5–10 中央 / 10–15 離す\n15–20 左 / 20–25 右 / 25–30 離す', font=('Yu Gothic UI', 13)).pack()
    phases = ['無荷重：手を離す', '中央を手で押す', '手を離す', '左側を手で押す', '右側を手で押す', '手を離す']
    last = None
    done_at = None
    def tick():
        nonlocal last, done_at
        button.configure(state='normal' if ui.ready and ui.start_ns is None and not ui.done else 'disabled')
        if ui.done:
            label.configure(text=ui.status)
            if done_at is None:
                done_at = time.perf_counter()
                winsound.PlaySound('SystemAsterisk', winsound.SND_ALIAS | winsound.SND_ASYNC)
            if time.perf_counter()-done_at >= 8:
                window.destroy()
                return
        elif ui.start_ns is None:
            label.configure(text=ui.status)
        else:
            elapsed = (time.perf_counter_ns()-ui.start_ns)/1e9
            if elapsed < 0:
                label.configure(text=f'開始まで {int(-elapsed)+1} 秒：手を離してください')
            elif elapsed < 30:
                phase = int(elapsed//5)
                label.configure(text=phases[phase])
                detail.configure(text=f'{elapsed:.1f} / 30秒　BLE {ui.receiver.sequence}件　ACK {ui.receiver.acked}件')
                if last != phase:
                    last = phase
                    winsound.PlaySound('SystemAsterisk', winsound.SND_ALIAS | winsound.SND_ASYNC)
            else:
                label.configure(text='終了：手を離してください。TCP送達確認中…')
        window.after(50, tick)
    def close():
        if ui.done:
            window.destroy()
        elif ui.start_ns is None:
            ui.cancel = True
    window.protocol('WM_DELETE_WINDOW', close)
    thread.start()
    tick()
    window.mainloop()
    thread.join()
