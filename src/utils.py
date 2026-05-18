"""Funções utilitárias do projeto."""

from pathlib import Path

import torch
from torchvision import models

CLASSES = ["cortar", "nao_cortar"]


def get_device() -> torch.device:
    """Retorna GPU, quando disponível, ou CPU."""
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


<<<<<<< HEAD
def create_model(num_classes: int = 2) -> torch.nn.Module:
    """Cria um modelo ResNet18 usando transfer learning."""
    weights = models.ResNet18_Weights.DEFAULT
=======
def create_model(num_classes: int = 2, pretrained: bool = True) -> torch.nn.Module:
    """Cria um modelo ResNet18 para classificacao binaria."""
    weights = models.ResNet18_Weights.DEFAULT if pretrained else None
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
    model = models.resnet18(weights=weights)

    for parameter in model.parameters():
        parameter.requires_grad = False

    in_features = model.fc.in_features
    model.fc = torch.nn.Linear(in_features, num_classes)
    return model


def ensure_directory(path: str | Path) -> None:
    """Cria um diretório caso ele ainda não exista."""
    Path(path).mkdir(parents=True, exist_ok=True)
