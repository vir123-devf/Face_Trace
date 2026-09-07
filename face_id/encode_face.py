from deepface import DeepFace

MODEL_NAME = "Facenet512"


def encode_face(image_path, enforce_detection: bool = True) -> dict:
    result = DeepFace.represent(
        img_path=image_path,
        model_name=MODEL_NAME,
        enforce_detection=enforce_detection,
        detector_backend="opencv",
    )[0]

    source = image_path if isinstance(image_path, str) else "<array>"
    return {
        "embedding": result["embedding"],
        "face_area": result.get("facial_area"),
        "model": MODEL_NAME,
        "source_image": source,
    }


if __name__ == "__main__":
    import json
    import sys

    face = encode_face(sys.argv[1])
    print(json.dumps({"model": face["model"], "vector_length": len(face["embedding"])}, indent=2))
