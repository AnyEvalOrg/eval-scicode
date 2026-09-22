FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends procps util-linux hostname \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 scicode-candidate \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin scicode-candidate
COPY scicode/docker-requirements.txt /opt/scicode/requirements.txt
RUN pip install --no-cache-dir -r /opt/scicode/requirements.txt
COPY scicode/test_data.h5 /opt/scicode/test_data.h5
RUN echo '48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890  /opt/scicode/test_data.h5' | sha256sum -c - \
    && chown root:root /opt/scicode/test_data.h5 && chmod 0400 /opt/scicode/test_data.h5
COPY scicode/test_plan.py scicode/test_util.py scicode/process_data.py scicode/safe_serialization.py scicode/comparison_worker.py /opt/scicode/runtime/
RUN chmod -R go-w /opt/scicode && python3 -c 'import numpy, scipy, sympy, h5py, pandas, matplotlib; from scipy.integrate import simps'
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 0:0
WORKDIR /tmp
CMD ["sleep", "infinity"]
