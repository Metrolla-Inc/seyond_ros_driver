# syntax=docker/dockerfile:1
FROM ros:jazzy-perception-noble

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=jazzy

WORKDIR /opt/metrolla/seyond
COPY . .

RUN apt-get update && apt-get install -y \
    iproute2 \
    libyaml-cpp-dev \
    python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*

# Build the driver
RUN bash -c "source /opt/ros/jazzy/setup.bash && ./build.bash"

RUN chmod +x entrypoint.sh && cp entrypoint.sh /usr/local/bin/
ENTRYPOINT ["entrypoint.sh"]
# Default command to run the driver
CMD ["ros2", "launch", "seyond", "start.py"]