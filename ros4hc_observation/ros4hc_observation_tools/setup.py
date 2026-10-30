from setuptools import find_packages, setup

setup(
    name='ros4hc_observation_tools',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/ros4hc_observation_tools']),
        ('share/ros4hc_observation_tools', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ROS4HC contributors',
    maintainer_email='kupper.lucas@gmail.com',
    description='Synthetic observations and structural validation.',
    license='GPL-3.0-or-later',
    tests_require=['pytest'],
    entry_points={'console_scripts': [
        'synthetic_hr = ros4hc_observation_tools.synthetic_hr:main',
        'observation_validator = ros4hc_observation_tools.validator:main',
    ]},
)
