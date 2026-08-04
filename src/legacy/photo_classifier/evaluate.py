"""Avaliacao do classificador legado de fotos RGB."""

import argparse


def evaluate(
    data_dir: str,
    model_path: str,
    batch_size: int,
    image_size: int,
    split: str,
    num_workers: int,
) -> None:
    import matplotlib.pyplot as plt
    import torch
    from sklearn.metrics import ConfusionMatrixDisplay, classification_report, confusion_matrix

    from .dataset import create_dataloader
    from .utils import CLASSES, create_model, ensure_directory, get_device

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

    labels = list(range(len(CLASSES)))
    print(classification_report(true_labels, predicted_labels, labels=labels, target_names=CLASSES, zero_division=0))

    matrix = confusion_matrix(true_labels, predicted_labels, labels=labels)
    display = ConfusionMatrixDisplay(confusion_matrix=matrix, display_labels=CLASSES)
    display.plot(cmap="Blues")

    ensure_directory("outputs")
    output_path = f"outputs/matriz_confusao_{split}.png"
    plt.savefig(output_path, bbox_inches="tight")
    print(f"Matriz de confusao salva em: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia o classificador legado de fotos RGB.")
    parser.add_argument("--data-dir", default="data", help="Diretorio raiz com train/val/test.")
    parser.add_argument("--model-path", default="models/photo_classifier_resnet18.pth", help="Caminho do modelo treinado.")
    parser.add_argument("--batch-size", type=int, default=8, help="Tamanho do lote.")
    parser.add_argument("--image-size", type=int, default=224, help="Tamanho das imagens de entrada.")
    parser.add_argument("--split", default="test", choices=["train", "val", "test"], help="Particao avaliada.")
    parser.add_argument("--num-workers", type=int, default=0, help="Processos auxiliares para carregar dados.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    evaluate(args.data_dir, args.model_path, args.batch_size, args.image_size, args.split, args.num_workers)


if __name__ == "__main__":
    main()
