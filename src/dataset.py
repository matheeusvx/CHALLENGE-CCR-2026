"""Dataset simples para imagens organizadas por classe."""

from pathlib import Path

import cv2
import torch
from torch.utils.data import Dataset
from torchvision import transforms

from utils import CLASSES


class RoadVegetationDataset(Dataset):
    """Carrega imagens das pastas data/cortar e data/nao_cortar."""

    def __init__(self, data_dir: str, image_paths: list[Path] | None = None):
        self.data_dir = Path(data_dir)
        self.transform = transforms.Compose(
            [
                transforms.ToPILImage(),
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )

        self.samples = self._load_samples(image_paths)

    def _load_samples(self, image_paths: list[Path] | None) -> list[tuple[Path, int]]:
        if image_paths is not None:
            return [(path, CLASSES.index(path.parent.name)) for path in image_paths]

        samples = []
        for label, class_name in enumerate(CLASSES):
            class_dir = self.data_dir / class_name
            for image_path in sorted(class_dir.glob("*")):
                if image_path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                    samples.append((image_path, label))
        return samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        image_path, label = self.samples[index]
        image = cv2.imread(str(image_path))

        if image is None:
            raise ValueError(f"Não foi possível ler a imagem: {image_path}")

        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = self.transform(image)
        return image, label


def list_image_paths(data_dir: str) -> list[Path]:
    """Lista os caminhos das imagens do projeto."""
    paths = []
    for class_name in CLASSES:
        class_dir = Path(data_dir) / class_name
        paths.extend(
            sorted(
                path
                for path in class_dir.glob("*")
                if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
            )
        )
    return paths
