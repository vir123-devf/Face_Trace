import os
import subprocess
import sys
from pathlib import Path

_VENV_PY = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
if _VENV_PY.exists() and Path(sys.executable).resolve() != _VENV_PY.resolve():
    raise SystemExit(subprocess.call([str(_VENV_PY), *sys.argv]))

os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from face_id.crop_face import crop_face
from face_id.visual_embed import MODEL_NAME, embed_image_file
from face_id.similarity import cosine_similarity, score_matches
from search.web_search import find_matching_posts
from evidence.hasher import build_evidence_bundle, hash_bundle
from chain.client import get_web3, deploy_contract, submit_hash, is_verified

# MobileNetV2 cosine similarity between the input image and retrieved web images.
CONFIDENCE_THRESHOLD = 0.55
TOP_RESULTS = 10


def _print_top_matches(matches: list[dict]) -> None:
    print(
        f"Cosine similarity: cropped face vs each web-search image ({MODEL_NAME}):"
    )
    for index, match in enumerate(matches, start=1):
        cosine = match.get("cosine_similarity")
        cosine_text = f"{cosine:.6f}" if isinstance(cosine, (int, float)) else "n/a"
        compared = match.get("compared_image_url") or match.get("url")
        print(f"  {index}. cosine(crop, web image) = {cosine_text}")
        print(f"      {compared}")
    print()


def run(image_path: str, auto_confirm: bool = False):
    print("Stage 1: cropping the face from the input image...")
    crop = crop_face(image_path)
    cropped_path = crop["cropped_path"]
    print(f"Detected {crop['face_count']} face(s); saved crop to {cropped_path}")

    print(f"Stage 2: {MODEL_NAME} embeddings for input image and cropped face...")
    input_data = embed_image_file(image_path)
    crop_data = embed_image_file(cropped_path)
    crop_vs_input = cosine_similarity(input_data["embedding"], crop_data["embedding"])
    print(f"Cosine(input image, cropped face) = {crop_vs_input:.6f}")

    image_data = {
        "source_image": image_path,
        "cropped_image": cropped_path,
        "face_area": crop["face_area"],
        "model": MODEL_NAME,
        "embedding": crop_data["embedding"],
        "input_embedding": input_data["embedding"],
        "cosine_input_vs_crop": round(crop_vs_input, 6),
    }

    print("Stage 3: searching the web for visually similar images (from the crop)...")
    matches = find_matching_posts(cropped_path)
    if not matches:
        print("No live matching social-media post found. Stopping.")
        return

    print("Stage 4: cosine(cropped face, each web-search image)...")
    matches = score_matches(crop_data["embedding"], matches)[:TOP_RESULTS]
    _print_top_matches(matches)
    match = matches[0]
    confidence = match.get("cosine_similarity")
    if confidence is None:
        print("Could not download/compare any retrieved images. Stopping.")
        return
    print(f"Best match: {match.get('compared_image_url') or match['url']}")
    print(f"cosine(crop, web image) = {confidence:.6f}  (threshold {CONFIDENCE_THRESHOLD})")
    if confidence < CONFIDENCE_THRESHOLD:
        print("Similarity too low. Stopping before upload.")
        return

    if auto_confirm:
        print("Upload this match to the blockchain as verified? (y/n): y")
        confirm = "y"
    else:
        confirm = input("Upload this match to the blockchain as verified? (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled by user.")
        return

    print("Stage 5: building and hashing evidence bundle...")
    bundle = build_evidence_bundle(image_data, match, confidence, matches)
    bundle_hash = hash_bundle(bundle)
    print(f"Evidence hash: {bundle_hash}")

    print("Stage 6: uploading hash to blockchain...")
    w3 = get_web3()
    account = w3.eth.accounts[0]
    contract, address = deploy_contract(w3, account)
    submit_hash(w3, contract, account, bundle_hash)
    print(f"Contract deployed at: {address}")

    print("Stage 7: re-verifying...")
    verified = is_verified(contract, bundle_hash)
    print(f"On-chain verification result: {verified}")

    print("\nTamper check: re-hashing evidence with one field changed...")
    tampered_bundle = dict(bundle)
    tampered_bundle["confidence"] = 0.01
    tampered_hash = hash_bundle(tampered_bundle)
    tampered_result = is_verified(contract, tampered_hash)
    print(f"Tampered hash verification result (should be False): {tampered_result}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--yes"]
    auto_confirm = "--yes" in sys.argv[1:]
    if len(args) != 1:
        print("Usage: python demo.py [--yes] <path_to_image>")
        sys.exit(1)

    run(args[0], auto_confirm=auto_confirm)
