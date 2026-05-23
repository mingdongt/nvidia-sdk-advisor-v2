import tempfile
from pathlib import Path

from src.vector_search import VectorStore


def test_upsert_and_search_roundtrip():
    """End-to-end: write 3 docs, query, get the most similar one."""
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore(persist_dir=Path(tmp), collection="test")
        try:
            store.upsert(
                ids=["a", "b", "c"],
                texts=[
                    "Jetson Orin Nano can run YOLOv8 at 30fps with TensorRT INT8 quantization.",
                    "Holoscan SDK is for medical imaging on Clara AGX devkit.",
                    "ROS2 with Isaac ROS provides SLAM and visual odometry.",
                ],
                metadatas=[{"source": "test"}, {"source": "test"}, {"source": "test"}],
            )
            results = store.search("object detection model on Jetson Nano", k=3)
            assert len(results) == 3
            # Best match should be the YOLO one
            assert results[0]["id"] == "a"
        finally:
            store.close()


def test_metadata_filter():
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore(persist_dir=Path(tmp), collection="test2")
        try:
            store.upsert(
                ids=["1", "2"],
                texts=["foo", "bar"],
                metadatas=[{"source": "a"}, {"source": "b"}],
            )
            results = store.search("foo", k=10, where={"source": "b"})
            assert len(results) == 1
            assert results[0]["id"] == "2"
        finally:
            store.close()
