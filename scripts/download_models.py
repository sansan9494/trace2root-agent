from __future__ import annotations

from pathlib import Path

from sentence_transformers import CrossEncoder, SentenceTransformer

from backend.app.config import get_settings


def main() -> None:
    settings = get_settings()
    cache = Path(settings.model_cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Downloading embedding model to {cache}: {settings.embedding_model}")
    embedding = SentenceTransformer(
        settings.embedding_model,
        device=settings.embedding_device,
        cache_folder=str(cache),
    )
    dimension = embedding.get_embedding_dimension()
    if dimension != settings.embedding_dimension:
        raise RuntimeError(f"模型维度 {dimension} 与配置 {settings.embedding_dimension} 不一致")
    sample = embedding.encode(["支付服务高延迟"], normalize_embeddings=True)
    print(f"embedding ready: dimension={dimension}, sample_shape={sample.shape}")

    print(f"Downloading reranker model to {cache}: {settings.reranker_model}")
    reranker = CrossEncoder(
        settings.reranker_model,
        device=settings.reranker_device,
        max_length=512,
        trust_remote_code=False,
        cache_folder=str(cache),
    )
    score = reranker.predict(
        [("支付服务高延迟", "支付服务 P95 延迟超过 800ms 时检查上游依赖")],
        batch_size=1,
        show_progress_bar=False,
    )
    print(f"reranker ready: sample_score={float(score[0]):.4f}")


if __name__ == "__main__":
    main()
