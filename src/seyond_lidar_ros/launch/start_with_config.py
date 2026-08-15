from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.actions import SetEnvironmentVariable
from ament_index_python.packages import get_package_share_directory

import os


def _env_flag(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def generate_launch_description():
    share = get_package_share_directory('seyond')
    yaml_config = share + '/config.yaml'
    if _env_flag('DUAL_LIDAR'):
        dual_config = share + '/two-config.yaml'
        if os.path.isfile(dual_config):
            yaml_config = dual_config
        else:
            # missing dual config must not crash-loop the driver
            print('[start_with_config] DUAL_LIDAR is set but two-config.yaml is '
                  'not installed; falling back to single-lidar config.yaml', flush=True)


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
