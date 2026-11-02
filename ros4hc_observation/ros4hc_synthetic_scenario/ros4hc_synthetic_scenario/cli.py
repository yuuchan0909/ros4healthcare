"""日本語CLI。"""
import argparse
from pathlib import Path
import sys
from .dataset import export, generate, load_config


def default_config():
    local = Path(__file__).resolve().parents[1]/'configs/daily_subject_001.json'
    if local.exists():
        return local
    for prefix in sys.prefix, *sys.path:
        candidate = Path(prefix)/'share/ros4hc_synthetic_scenario/configs/daily_subject_001.json'
        if candidate.exists():
            return candidate
    # ROS配置ではモジュールとshareが同じインストールprefixに属する。
    for parent in Path(__file__).resolve().parents:
        candidate = parent/'share/ros4hc_synthetic_scenario/configs/daily_subject_001.json'
        if candidate.exists():
            return candidate
    raise FileNotFoundError('標準設定が見つかりません。--config を指定してください')


def main():
    parser = argparse.ArgumentParser(description='24時間・288窓の合成研究データを生成します')
    parser.add_argument('--config', type=Path, help='JSON設定ファイル')
    parser.add_argument('--output', type=Path, required=True, help='出力ディレクトリ')
    parser.add_argument('--seed', type=int, help='乱数seedを上書き')
    args = parser.parse_args()
    try:
        data = generate(load_config(args.config or default_config()), args.seed)
        export(data, args.output)
    except (ValueError, KeyError, OSError, AssertionError) as exc:
        parser.error(f'生成に失敗しました: {exc}')
    print(f'合成データ288窓を生成しました: {args.output}')
    print('Ground Truthと日本語要約は評価者専用です。生理学的・臨床的妥当性は主張しません。')


if __name__ == '__main__':
    main()
