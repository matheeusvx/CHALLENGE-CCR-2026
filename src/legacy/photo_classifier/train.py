"""Treinamento do classificador legado de fotos RGB."""

import argparse
from pathlib import Path


def train(
    data_dir: str,
    model_path: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    image_size: int,
    num_workers: int,
) -> None:
    import torch

    from .dataset import create_dataloaders
    from .utils import create_model, ensure_directory, get_device

    dataloaders = create_dataloaders(
        data_dir=data_dir,
        image_size=image_size,
        batch_size=batch_size,
        num_workers=num_workers,
        splits=("train", "val"),
    )

    device = get_device()
    model = create_model().to(device)
    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.fc.parameters(), lr=learning_rate)

    for epoch in range(epochs):
        model.train()
        train_loss = 0.0

        for images, labels in dataloaders["train"]:
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
            for images, labels in dataloaders["val"]:
                images = images.to(device)
                labels = labels.to(device)
                outputs = model(images)
                predictions = torch.argmax(outputs, dim=1)
                val_correct += (predictions == labels).sum().item()
                val_total += labels.size(0)

        val_accuracy = val_correct / val_total if val_total else 0.0
        print(f"Epoca {epoch + 1}/{epochs} - Loss: {train_loss:.4f} - Val Acc: {val_accuracy:.4f}")

    ensure_directory(Path(model_path).parent)
    torch.save(model.state_dict(), model_path)
    print(f"Modelo salvo em: {model_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Treina o classificador legado de fotos RGB.")
    parser.add_argument("--data-dir", default="data", help="Diretorio raiz com train/val/test.")
    parser.add_argument("--model-path", default="models/photo_classifier_resnet18.pth", help="Caminho para salvar o modelo.")
    parser.add_argument("--epochs", type=int, default=5, help="Quantidade de epocas de treinamento.")
    parser.add_argument("--batch-size", type=int, default=8, help="Tamanho do lote.")
    parser.add_argument("--learning-rate", type=float, default=0.001, help="Taxa de aprendizado.")
    parser.add_argument("--image-size", type=int, default=224, help="Tamanho das imagens de entrada.")
    parser.add_argument("--num-workers", type=int, default=0, help="Processos auxiliares para carregar dados.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    train(
        args.data_dir,
        args.model_path,
        args.epochs,
        args.batch_size,
        args.learning_rate,
        args.image_size,
        args.num_workers,
    )


if __name__ == "__main__":
    main()
