<<<<<<< HEAD
"""Avaliação do classificador de vegetação."""
=======
"""Avaliacao do classificador de vegetacao."""
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

import argparse

import matplotlib.pyplot as plt
import torch
from sklearn.metrics import ConfusionMatrixDisplay, classification_report, confusion_matrix
<<<<<<< HEAD
from torch.utils.data import DataLoader

from dataset import RoadVegetationDataset
from utils import CLASSES, create_model, ensure_directory, get_device


def evaluate(data_dir: str, model_path: str, batch_size: int) -> None:
    dataset = RoadVegetationDataset(data_dir)
    if len(dataset) == 0:
        raise ValueError("Nenhuma imagem encontrada para avaliação.")

    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)

    device = get_device()
    model = create_model()
=======

from dataset import create_dataloader
from utils import CLASSES, create_model, ensure_directory, get_device


def evaluate(
    data_dir: str,
    model_path: str,
    batch_size: int,
    image_size: int,
    split: str,
    num_workers: int,
) -> None:
    loader = create_dataloader(
        data_dir=data_dir,
        split=split,
        image_size=image_size,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
    )

    device = get_device()
    model = create_model(pretrained=False)
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.to(device)
    model.eval()

    true_labels = []
    predicted_labels = []

    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            outputs = model(images)
            predictions = torch.argmax(outputs, dim=1).cpu().tolist()

            predicted_labels.extend(predictions)
            true_labels.extend(labels.tolist())

    print(classification_report(true_labels, predicted_labels, target_names=CLASSES))

    matrix = confusion_matrix(true_labels, predicted_labels)
    display = ConfusionMatrixDisplay(confusion_matrix=matrix, display_labels=CLASSES)
    display.plot(cmap="Blues")

    ensure_directory("outputs")
<<<<<<< HEAD
    output_path = "outputs/matriz_confusao.png"
    plt.savefig(output_path, bbox_inches="tight")
    print(f"Matriz de confusão salva em: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia o classificador de vegetação.")
    parser.add_argument("--data-dir", default="data", help="Diretório com as pastas das classes.")
    parser.add_argument("--model-path", default="models/modelo_vegetacao.pth", help="Caminho do modelo treinado.")
    parser.add_argument("--batch-size", type=int, default=8, help="Tamanho do lote.")
=======
    output_path = f"outputs/matriz_confusao_{split}.png"
    plt.savefig(output_path, bbox_inches="tight")
    print(f"Matriz de confusao salva em: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia o classificador de vegetacao.")
    parser.add_argument("--data-dir", default="data", help="Diretorio raiz com train/val/test.")
    parser.add_argument("--model-path", default="models/modelo_vegetacao.pth", help="Caminho do modelo treinado.")
    parser.add_argument("--batch-size", type=int, default=8, help="Tamanho do lote.")
    parser.add_argument("--image-size", type=int, default=224, help="Tamanho das imagens de entrada.")
    parser.add_argument("--split", default="test", choices=["train", "val", "test"], help="Particao avaliada.")
    parser.add_argument("--num-workers", type=int, default=0, help="Processos auxiliares para carregar dados.")
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
<<<<<<< HEAD
    evaluate(args.data_dir, args.model_path, args.batch_size)
=======
    evaluate(args.data_dir, args.model_path, args.batch_size, args.image_size, args.split, args.num_workers)
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
