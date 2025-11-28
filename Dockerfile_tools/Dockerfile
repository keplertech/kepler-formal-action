FROM ubuntu:24.04 AS builder

#compile yosys
# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    bash \
    make \
    pkg-config \
    tcl-dev \
    libreadline-dev \
    libffi-dev \
    git \
    cmake \
    g++ \
    python3 \
    flex \
    bison \
    unzip \
    wget && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /
RUN git clone --recursive https://github.com/YosysHQ/yosys.git
WORKDIR /yosys
ENV PREFIX=/yosys-install
RUN make config-gcc
RUN make -j$(nproc)
RUN make install
#slang will need to fix version in the future
WORKDIR /
RUN git clone --recursive https://github.com/povik/yosys-slang
WORKDIR /yosys-slang
ENV YOSYS_PREFIX=/yosys-install/bin/
RUN make -j$(nproc)
RUN make install

#get yosys synthesis env from https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git
WORKDIR /or-scripts
RUN wget https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/archive/refs/heads/master.zip
RUN unzip master.zip
RUN mkdir najaeda-or
RUN mkdir najaeda-or/flow
RUN mkdir najaeda-or/flow/platforms
RUN cp -R OpenROAD-flow-scripts-master/flow/platforms/common najaeda-or/flow/platforms/common
RUN cp -R OpenROAD-flow-scripts-master/flow/platforms/nangate45 najaeda-or/flow/platforms/nangate45
RUN cp -R OpenROAD-flow-scripts-master/flow/scripts najaeda-or/flow/scripts
RUN cp -R OpenROAD-flow-scripts-master/flow/util najaeda-or/flow/util
RUN cp -R OpenROAD-flow-scripts-master/flow/Makefile najaeda-or/flow/Makefile

FROM ubuntu:24.04 AS najaeda

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tcl8.6 \
		make \
    time \
    python3 python3-pip python3-venv

COPY --from=builder /yosys-install /yosys
COPY --from=builder /or-scripts/najaeda-or /najaeda-or

# Create a virtual environment
RUN python3 -m venv /opt/venv

RUN /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install najaeda==0.1.21 \
 && /opt/venv/bin/pip install pyyaml

# Add the venv to PATH
ENV PATH="/opt/venv/bin:$PATH"
