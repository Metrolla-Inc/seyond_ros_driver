from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.actions import SetEnvironmentVariable
from ament_index_python.packages import get_package_share_directory

import os


def generate_launch_description():
    yaml_config = get_package_share_directory('seyond') + '/config.yaml'
    if os.environ.get('DUAL_LIDAR') is True or 1 or "1":
        yaml_config=get_package_share_directory('seyond')+'/two-config.yaml'


    return LaunchDescription(
        [
            # set log color
            SetEnvironmentVariable(name='RCUTILS_COLORIZED_OUTPUT', value='1'),

            DeclareLaunchArgument(
                'config_path',
                default_value=yaml_config,
                description='config path'
            ),
            

            Node(
                package="seyond",
                executable="seyond_node",
                parameters=[
                    {'config_path': LaunchConfiguration('config_path')},
                ],
            ),
        ]
    )
