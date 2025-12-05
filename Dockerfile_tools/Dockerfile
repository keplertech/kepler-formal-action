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
    python3-dev \
    libfl-dev \
    bison \
    libboost-dev \
    libtbb-dev \
    capnproto \
    libcapnp-dev \
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

#get kepler formal
WORKDIR /
RUN git clone --recursive https://github.com/keplertech/kepler-formal
WORKDIR /kepler-formal
RUN cmake -DENABLE_UNIT_TESTS=OFF -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_FLAGS_RELEASE="-Ofast -march=native \
  -ffast-math -flto" -DCMAKE_EXE_LINKER_FLAGS="-flto" \
  -DCMAKE_INSTALL_PREFIX=/kepler-formal-install \
  .
RUN make -j$(nproc)
RUN make install

FROM ubuntu:24.04 AS najaeda
#
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tcl8.6 \
		make \
    time \
    python3 python3-pip python3-venv \
    python3-dev \
    libcapnp-dev \
    libtbb-dev

COPY --from=builder /yosys-install /yosys
COPY --from=builder /kepler-formal-install /kepler-formal

# Register /kepler-formal/lib as a dynamic linker search path
RUN echo "/kepler-formal/lib" > /etc/ld.so.conf.d/kepler-formal.conf && \
    ldconfig


#
## Create a virtual environment
#RUN python3 -m venv /opt/venv
#
#RUN /opt/venv/bin/pip install --upgrade pip \
# && /opt/venv/bin/pip install najaeda==0.3.0 \
# && /opt/venv/bin/pip install pyyaml
#
## Add the venv to PATH
#ENV PATH="/opt/venv/bin:$PATH"
