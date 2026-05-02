FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Generate gRPC stubs from the shared proto file
COPY ../infra/proto/signals.proto ./proto/signals.proto
RUN python -m grpc_tools.protoc \
    -I./proto \
    --python_out=./proto \
    --grpc_python_out=./proto \
    signals.proto
EXPOSE 50052
CMD ["python", "main.py"]
