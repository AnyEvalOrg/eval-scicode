FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends procps util-linux hostname libseccomp2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 scicode-candidate \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin scicode-candidate
COPY scicode/docker-requirements.txt /opt/scicode/requirements.txt
RUN pip install --no-cache-dir -r /opt/scicode/requirements.txt
# SciCode-Verified v2 targets (MD5 2b41a7df40ddc23ce651ec05b8ecb6f8) at the path the runtime reads.
COPY scicode/test_data_cleaned.h5 /opt/scicode/test_data.h5
RUN echo '8fb6e575b7b6dda5e48b04dea338fc6af4fe185774b8f19221c96945df9b4142  /opt/scicode/test_data.h5' | sha256sum -c - \
    && chown root:root /opt/scicode/test_data.h5 && chmod 0400 /opt/scicode/test_data.h5 \
    && echo '8fb6e575b7b6dda5e48b04dea338fc6af4fe185774b8f19221c96945df9b4142' > /opt/scicode/test_data.sha256 \
    && chown root:root /opt/scicode/test_data.sha256 && chmod 0444 /opt/scicode/test_data.sha256
COPY scicode/bindings.py scicode/rpc_objects.py scicode/proxy_protocol.py scicode/candidate_worker.py scicode/test_util.py scicode/process_data.py scicode/safe_serialization.py scicode/comparison_worker.py scicode/expected_values.py /opt/scicode/runtime/
RUN chmod -R go-w /opt/scicode && python3 -c 'import numpy, scipy, sympy, h5py, pandas, matplotlib; from scipy.integrate import simps'
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 0:0
WORKDIR /tmp
CMD ["sleep", "infinity"]
