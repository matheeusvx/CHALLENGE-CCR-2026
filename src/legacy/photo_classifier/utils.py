"""Funcoes utilitarias do classificador legado de fotos RGB."""

from pathlib import Path

import torch
from torchvision import models

CLASSES = ["cortar", "nao_cortar"]


def get_device() -> torch.device:
    """Retorna GPU, quando disponivel, ou CPU."""
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def create_model(num_classes: int = 2, pretrained: bool = True) -> torch.nn.Module:
    """Cria uma ResNet18 para classificacao binaria por transfer learning."""
    weights = models.ResNet18_Weights.DEFAULT if pretrained else None
    model = models.resnet18(weights=weights)

    for parameter in model.parameters():
        parameter.requires_grad = False

    in_features = model.fc.in_features
    model.fc = torch.nn.Linear(in_features, num_classes)
    return model


def ensure_directory(path: str | Path) -> None:
    """Cria um diretorio caso ele ainda nao exista."""
    Path(path).mkdir(parents=True, exist_ok=True)

