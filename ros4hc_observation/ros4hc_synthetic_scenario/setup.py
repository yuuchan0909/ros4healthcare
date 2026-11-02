from setuptools import setup, find_packages
setup(name='ros4hc_synthetic_scenario', version='0.1.0', packages=find_packages(),
 data_files=[('share/ament_index/resource_index/packages', ['resource/ros4hc_synthetic_scenario']),
 ('share/ros4hc_synthetic_scenario', ['package.xml', 'README.md', 'PHASE5B.md', 'PHASE5C.md']),
 ('share/ros4hc_synthetic_scenario/configs', ['configs/daily_subject_001.json'])],
 install_requires=['setuptools'], tests_require=['pytest'], license='GPL-3.0-or-later',
 maintainer='ROS4HC contributors', maintainer_email='kupper.lucas@gmail.com',
 description='24時間の合成活動シナリオ評価データ',
 entry_points={'console_scripts':[
     'generate_daily_scenario = ros4hc_synthetic_scenario.cli:main',
     'generate_sensor_language_inputs = ros4hc_synthetic_scenario.sensor_language:main',
     'run_llm_experiment = ros4hc_synthetic_scenario.llm_experiment:main']})
