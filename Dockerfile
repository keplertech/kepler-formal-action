FROM ubuntu:22.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG KEPLER_FORMAL_REF=4bbc293c4450bc559849bb675fed62cd136715d1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    cmake \
    ninja-build \
    build-essential \
    pkg-config \
    libboost-dev \
    libboost-iostreams-dev \
    python3-dev \
    capnproto \
    libcapnp-dev \
    libtbb-dev \
    bison \
    flex \
    libfl-dev \
    doxygen \
    libspdlog-dev \
    libfmt-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --recurse-submodules \
    https://github.com/keplertech/kepler-formal.git kepler-formal \
    && cd kepler-formal \
    && git checkout --detach "${KEPLER_FORMAL_REF}" \
    && git submodule update --init --recursive

WORKDIR /src/kepler-formal

RUN cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_INSTALL_PREFIX=/opt/kepler-formal \
    -DENABLE_UNIT_TESTS=OFF \
    && cmake --build build --parallel \
    && cmake --install build

RUN mkdir -p /opt/kepler-dist/bin /opt/kepler-dist/lib /opt/kepler-dist/meta \
    && cp /opt/kepler-formal/bin/kepler-formal /opt/kepler-dist/bin/ \
    && find build -type f \( -name 'libnaja_*.so' -o -name 'libnaja_*.so.*' \) -exec cp -P {} /opt/kepler-dist/lib/ \; \
        && if find build -type f -name 'naja.so' | grep -q .; then \
            mkdir -p /opt/kepler-dist/lib/python; \
            find build -type f -name 'naja.so' -exec cp -P {} /opt/kepler-dist/lib/python/ \; ; \
       fi \
    && printf '%s\n' "${KEPLER_FORMAL_REF}" > /opt/kepler-dist/meta/upstream-ref \
    && if ! find /opt/kepler-dist/lib -maxdepth 1 -type f -name 'libnaja_*.so*' | grep -q .; then \
         echo 'Expected Naja shared libraries were not found in the CMake build output.' >&2; \
         exit 1; \
       fi \
    && cat > /opt/kepler-dist/kepler-formal <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"

export LD_LIBRARY_PATH="${script_dir}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
if [ -d "${script_dir}/lib/python" ]; then
  export PYTHONPATH="${script_dir}/lib/python${PYTHONPATH:+:${PYTHONPATH}}"
fi

exec "${script_dir}/bin/kepler-formal" "$@"
EOF

RUN chmod +x /opt/kepler-dist/kepler-formal

FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    python3 \
    libpython3.10 \
    libboost-dev \
    libboost-iostreams-dev \
    capnproto \
    libcapnp-dev \
    libtbb-dev \
    libspdlog-dev \
    libfmt-dev \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/kepler-dist /opt/kepler-formal
COPY entrypoint.sh /entrypoint.sh

ENV PATH="/opt/kepler-formal:${PATH}"

ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]