from setuptools import find_packages, setup

setup(
    name='sensomative_raw_bridge', version='0.1.0', packages=find_packages(),
    data_files=[('share/ament_index/resource_index/packages', ['resource/sensomative_raw_bridge']),
                ('share/sensomative_raw_bridge', ['package.xml', 'README.md'])],
    install_requires=['setuptools'],
    entry_points={'console_scripts': [
        'sensomative_reference_adapter = sensomative_raw_bridge.reference_adapter:main',
        'tcp_receiver = sensomative_raw_bridge.receiver:main',
        'verify_bag = sensomative_raw_bridge.verify_bag:main',
    ]},
    license='Apache-2.0',
)
