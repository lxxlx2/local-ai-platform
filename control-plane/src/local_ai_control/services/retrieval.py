"""On-demand embedding/reranking interfaces; no Learning Engine coupling."""
from dataclasses import dataclass
from typing import Protocol
@dataclass(frozen=True)
class RankedDocument: index:int; score:float
class EmbeddingProvider(Protocol):
    def embed(self,texts:list[str])->list[list[float]]: ...
class RerankProvider(Protocol):
    def rerank(self,query:str,documents:list[str])->list[RankedDocument]: ...
