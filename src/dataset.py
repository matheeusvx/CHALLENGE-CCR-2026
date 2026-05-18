<<<<<<< HEAD
"""Dataset simples para imagens organizadas por classe."""
=======
"""Pipeline de dataset e dataloaders para classificacao binaria de imagens.

A estrutura esperada e:

data/
  train/
    cortar/
    nao_cortar/
  val/
    cortar/
    nao_cortar/
  test/
    cortar/
    nao_cortar/
"""
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

from pathlib import Path

import cv2
import torch
<<<<<<< HEAD
from torch.utils.data import Dataset
=======
from torch.utils.data import DataLoader, Dataset
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
from torchvision import transforms

from utils import CLASSES

<<<<<<< HEAD

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
=======
# Extensoes aceitas. A lista e pequena para manter o comportamento previsivel.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def read_image_with_opencv(image_path: str | Path):
    """Le uma imagem local com OpenCV e converte de BGR para RGB."""
    image_path = Path(image_path)
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(f"Nao foi possivel ler a imagem: {image_path}")

    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def get_train_transforms(image_size: int = 224) -> transforms.Compose:
    """Define transformacoes simples para treino."""
    return transforms.Compose(
        [
            transforms.ToPILImage(),
            transforms.Resize((image_size, image_size)),
            # Pequenas variacoes ajudam o modelo sem complicar o pipeline.
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomRotation(degrees=10),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


def get_eval_transforms(image_size: int = 224) -> transforms.Compose:
    """Define transformacoes para validacao, teste e predicao."""
    return transforms.Compose(
        [
            transforms.ToPILImage(),
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


class RoadVegetationDataset(Dataset):
    """Dataset para imagens separadas em pastas por classe."""

    def __init__(
        self,
        root_dir: str | Path,
        transform: transforms.Compose | None = None,
        classes: list[str] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.classes = classes or CLASSES
        self.class_to_idx = {class_name: index for index, class_name in enumerate(self.classes)}
        self.transform = transform or get_eval_transforms()
        self.samples = self._find_samples()

    def _find_samples(self) -> list[tuple[Path, int]]:
        """Localiza imagens dentro das pastas das classes configuradas."""
        samples: list[tuple[Path, int]] = []

        for class_name in self.classes:
            class_dir = self.root_dir / class_name
            if not class_dir.exists():
                continue

            label = self.class_to_idx[class_name]
            for image_path in sorted(class_dir.iterdir()):
                if image_path.is_file() and image_path.suffix.lower() in IMAGE_EXTENSIONS:
                    samples.append((image_path, label))

>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
        return samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        image_path, label = self.samples[index]
<<<<<<< HEAD
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
=======
        image = read_image_with_opencv(image_path)

        if self.transform is not None:
            image = self.transform(image)

        return image, label


def create_dataloader(
    data_dir: str | Path,
    split: str,
    image_size: int = 224,
    batch_size: int = 8,
    shuffle: bool | None = None,
    num_workers: int = 0,
) -> DataLoader:
    """Cria um DataLoader para um split especifico."""
    split_dir = Path(data_dir) / split
    transform = get_train_transforms(image_size) if split == "train" else get_eval_transforms(image_size)

    dataset = RoadVegetationDataset(root_dir=split_dir, transform=transform)
    if len(dataset) == 0:
        raise ValueError(f"Nenhuma imagem encontrada em: {split_dir}")

    if shuffle is None:
        shuffle = split == "train"

    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers)


def create_dataloaders(
    data_dir: str | Path = "data",
    image_size: int = 224,
    batch_size: int = 8,
    num_workers: int = 0,
    splits: tuple[str, ...] = ("train", "val", "test"),
) -> dict[str, DataLoader]:
    """Cria DataLoaders para treino, validacao e teste."""
    return {
        split: create_dataloader(
            data_dir=data_dir,
            split=split,
            image_size=image_size,
            batch_size=batch_size,
            shuffle=(split == "train"),
            num_workers=num_workers,
        )
        for split in splits
    }
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
