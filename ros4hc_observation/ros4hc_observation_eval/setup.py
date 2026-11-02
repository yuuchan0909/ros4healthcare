from setuptools import find_packages, setup

setup(
    name='ros4hc_observation_eval', version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/ros4hc_observation_eval']),
        ('share/ros4hc_observation_eval', ['package.xml', 'README.md']),
    ],
    install_requires=['setuptools'], zip_safe=True,
    maintainer='ROS4HC contributors', maintainer_email='kupper.lucas@gmail.com',
    description='Observationの意味情報・保持・処理コストを調べる研究評価。',
    license='GPL-3.0-or-later', tests_require=['pytest'],
    entry_points={'console_scripts': [
        'observation_eval_benchmark = ros4hc_observation_eval.benchmark:main',
        'observation_eval_demo = ros4hc_observation_eval.demo:main',
    ]},
)
