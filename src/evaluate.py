"""Avaliação do classificador de vegetação."""

import argparse

import matplotlib.pyplot as plt
import torch
from sklearn.metrics import ConfusionMatrixDisplay, classification_report, confusion_matrix
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
    output_path = "outputs/matriz_confusao.png"
    plt.savefig(output_path, bbox_inches="tight")
    print(f"Matriz de confusão salva em: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia o classificador de vegetação.")
    parser.add_argument("--data-dir", default="data", help="Diretório com as pastas das classes.")
    parser.add_argument("--model-path", default="models/modelo_vegetacao.pth", help="Caminho do modelo treinado.")
    parser.add_argument("--batch-size", type=int, default=8, help="Tamanho do lote.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    evaluate(args.data_dir, args.model_path, args.batch_size)
