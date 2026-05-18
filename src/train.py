"""Treinamento do classificador de vegetação."""

import argparse
from pathlib import Path

import torch
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader

from dataset import RoadVegetationDataset, list_image_paths
from utils import create_model, ensure_directory, get_device


def train(data_dir: str, model_path: str, epochs: int, batch_size: int, learning_rate: float) -> None:
    image_paths = list_image_paths(data_dir)
    if not image_paths:
        raise ValueError("Nenhuma imagem encontrada em data/cortar ou data/nao_cortar.")

    train_paths, val_paths = train_test_split(image_paths, test_size=0.2, random_state=42)

    train_dataset = RoadVegetationDataset(data_dir, train_paths)
    val_dataset = RoadVegetationDataset(data_dir, val_paths)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    device = get_device()
    model = create_model().to(device)
    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.fc.parameters(), lr=learning_rate)

    for epoch in range(epochs):
        model.train()
        train_loss = 0.0

        for images, labels in train_loader:
            images = images.to(device)
            labels = labels.to(device)

            optimizer.zero_grad()
            outputs = model(images)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()

        val_correct = 0
        val_total = 0
        model.eval()
        with torch.no_grad():
            for images, labels in val_loader:
                images = images.to(device)
                labels = labels.to(device)
                outputs = model(images)
                predictions = torch.argmax(outputs, dim=1)
                val_correct += (predictions == labels).sum().item()
                val_total += labels.size(0)

        val_accuracy = val_correct / val_total if val_total else 0
        print(f"Época {epoch + 1}/{epochs} - Loss: {train_loss:.4f} - Val Acc: {val_accuracy:.4f}")

    ensure_directory(Path(model_path).parent)
    torch.save(model.state_dict(), model_path)
    print(f"Modelo salvo em: {model_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Treina o classificador de vegetação.")
    parser.add_argument("--data-dir", default="data", help="Diretório com as pastas das classes.")
    parser.add_argument("--model-path", default="models/modelo_vegetacao.pth", help="Caminho para salvar o modelo.")
    parser.add_argument("--epochs", type=int, default=5, help="Quantidade de épocas de treinamento.")
    parser.add_argument("--batch-size", type=int, default=8, help="Tamanho do lote.")
    parser.add_argument("--learning-rate", type=float, default=0.001, help="Taxa de aprendizado.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    train(args.data_dir, args.model_path, args.epochs, args.batch_size, args.learning_rate)
