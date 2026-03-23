from setuptools import find_packages, setup

package_name = 'livekit_bridge'

# Do not set python_requires: colcon introspects setup() and runs ast.literal_eval()
# on the Distribution dict; setuptools stores python_requires as a SpecifierSet, which
# breaks that path.

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=[
        'livekit>=1.0,<2',
        'livekit-api>=1.0,<2',
        'av>=12',
        'python-dotenv>=1.0',
    ],
    zip_safe=False,
    maintainer='dev',
    maintainer_email='dev@example.com',
    description='Bridge ROS 2 H.264 topics to LiveKit.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'livekit_publisher = livekit_bridge.livekit_publisher:main',
        ],
    },
)
