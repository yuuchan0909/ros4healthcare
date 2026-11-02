"""WSL orchestration of the real 30s Windows BLE → ROS → rosbag trial.

Run from a sourced ROS environment. Writes only to the explicitly selected data
directory. No sudo, driver or network configuration changes are performed.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def unc(path):
    return subprocess.check_output(['wslpath','-w',str(path)],text=True).strip()


def stop(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid,signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Process did not stop cleanly: '+str(process.args))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data-root',default=str(Path.home()/'ros2_ws/data/sensomative'))
    p.add_argument('--windows-python',required=True,help='WSL path to Windows python.exe')
    p.add_argument('--windows-deps',required=True,help='WSL path to isolated Windows dependencies')
    p.add_argument('--host',default='127.0.0.1',help='WSL TCP server address as seen from Windows')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--address',help='BLE address; required unless --replay-log is used')
    p.add_argument('--replay-log',help='Replay an original audit to a new receiver/bag instead of accessing BLE')
    args=p.parse_args()
    if not args.replay_log and not (args.address and args.address.strip()):
        p.error('--address is required for BLE capture')
    root=Path(args.data_root).expanduser()
    tag=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
    run_id='sensomative_raw_'+tag+('_replay' if args.replay_log else '')
    run=root/'raw'/run_id
    run.mkdir(parents=True,exist_ok=False)
    (root/'bags').mkdir(parents=True,exist_ok=True)
    (root/'reports').mkdir(parents=True,exist_ok=True)
    bag=root/'bags'/run_id
    report=root/'reports'/(run_id+'_verification.json')
    receiver_log=run/'wsl.jsonl'
    windows_log=run/'windows.jsonl'
    paths=dict(run=str(run),bag=str(bag),report=str(report),windows_log=str(windows_log),receiver_log=str(receiver_log))
    (run/'paths.json').write_text(json.dumps(paths,indent=2))
    print(json.dumps(paths),flush=True)
    env=dict(os.environ,ROS_LOCALHOST_ONLY='1')
    server=recorder=windows=None
    handles=[]
    def launch(argv,name):
        f=(run/(name+'.console.log')).open('x',encoding='utf-8')
        handles.append(f)
        return subprocess.Popen(argv,stdout=f,stderr=subprocess.STDOUT,env=env,start_new_session=True)
    try:
        server=launch(['ros2','run','sensomative_raw_bridge','tcp_receiver',
                       '--host','0.0.0.0','--port',str(args.port),'--require-subscriber','--log',str(receiver_log)],'receiver')
        deadline=time.monotonic()+20
        while not receiver_log.exists() or 'listening' not in receiver_log.read_text():
            if server.poll() is not None or time.monotonic()>deadline:
                raise RuntimeError('WSL receiver startup failed; see console log')
            time.sleep(.1)
        recorder=launch(['ros2','bag','record','--storage','sqlite3','-o',str(bag),'/sensomative/raw'],'bag')
        deadline=time.monotonic()+20
        while 'Subscribed to topic' not in (run/'bag.console.log').read_text():
            if recorder.poll() is not None or time.monotonic()>deadline:
                raise RuntimeError('rosbag subscriber startup failed; see console log')
            time.sleep(.1)
        time.sleep(1)
        script=Path(__file__).resolve().parents[1]/'windows/sensomative_ble_receiver.py'
        command=[args.windows_python,unc(script),'--deps',unc(Path(args.windows_deps)),
                        '--host',args.host,'--port',str(args.port),
                        '--log',unc(windows_log)]
        if args.address:
            command+=['--address',args.address]
        if args.replay_log:
            command+=['--replay-log',unc(Path(args.replay_log).expanduser())]
        else:
            command+=['--seconds','30','--guided']
        windows=launch(command,'windows')
        print('Recovery replay started.' if args.replay_log else 'Windows GUI launched. Click Start when ready. Bag subscriber is already running.',flush=True)
        while windows.poll() is None:
            if server.poll() is not None or recorder.poll() is not None:
                raise RuntimeError('ROS receiver or recorder exited during capture')
            if windows_log.exists():
                # A Windows writer can be between writing a row and its newline.
                text=windows_log.read_text()
                rows=[json.loads(s) for s in text.split('\n')[:-1] if s]
                originals=sum(r['kind']=='ble_notification' for r in rows)
                acked=sum(r['kind']=='tcp_ack' and r['type']=='notification' for r in rows)
                print(f'BLE={originals} ACK={acked}',flush=True)
            time.sleep(5)
        if windows.returncode:
            raise RuntimeError('Windows receiver failed; see windows.console.log')
        time.sleep(2)
    finally:
        # A live Windows acquisition must not be killed without a drained audit.
        if windows is not None and windows.poll() is None:
            print('Windows is still running. Finish its GUI trial before closing the ROS receiver.',flush=True)
            windows.wait()
        stop(recorder)
        stop(server)
        for f in handles:
            f.close()
    verify=['ros2','run','sensomative_raw_bridge','verify_bag',
            '--windows-log',args.replay_log or str(windows_log),'--receiver-log',str(receiver_log),
            '--bag',str(bag),'--report',str(report)]
    if args.replay_log:
        verify+=['--transport-log',str(windows_log)]
    result=subprocess.run(verify,env=env)
    raise SystemExit(result.returncode)


if __name__=='__main__':
    main()
