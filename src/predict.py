<<<<<<< HEAD
"""Predição de uma imagem individual."""
=======
"""Predicao de uma imagem individual."""
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

import argparse
from pathlib import Path

<<<<<<< HEAD
import cv2
import torch
from torchvision import transforms

from utils import CLASSES, create_model, get_device


def predict(image_path: str, model_path: str) -> str:
    device = get_device()
    model = create_model()
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.to(device)
    model.eval()

    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Não foi possível ler a imagem: {image_path}")

    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    transform = transforms.Compose(
        [
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )

    tensor = transform(image).unsqueeze(0).to(device)
    with torch.no_grad():
        output = model(tensor)
        prediction = torch.argmax(output, dim=1).item()

    return CLASSES[prediction]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classifica uma imagem de vegetação.")
    parser.add_argument("--image", required=True, help="Caminho da imagem para classificação.")
    parser.add_argument("--model-path", default="models/modelo_vegetacao.pth", help="Caminho do modelo treinado.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = predict(args.image, args.model_path)
    print(f"Imagem: {Path(args.image).name}")
    print(f"Classe prevista: {result}")
=======
import torch

from dataset import IMAGENET_MEAN, IMAGENET_STD, get_eval_transforms, read_image_with_opencv
from utils import CLASSES, create_model, get_device


def load_trained_model(model_path: str | Path) -> torch.nn.Module:
    """Carrega o modelo salvo em disco ja preparado para inferencia."""
    model_path = Path(model_path)
    if not model_path.exists():
        raise FileNotFoundError(f"Modelo nao encontrado: {model_path}")

    device = get_device()
    model = create_model(pretrained=False)
    checkpoint = torch.load(model_path, map_location=device)

    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        checkpoint = checkpoint["model_state_dict"]

    model.load_state_dict(checkpoint)
    model.to(device)
    model.eval()
    return model


def predict(image_path: str | Path, model_path: str | Path, image_size: int = 224) -> tuple[str, float]:
    """Retorna a classe prevista e a probabilidade da imagem informada."""
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Imagem nao encontrada: {image_path}")

    device = get_device()
    model = load_trained_model(model_path)
    image = read_image_with_opencv(image_path)
    transform = get_eval_transforms(image_size=image_size)
    tensor = transform(image).unsqueeze(0).to(device)

    with torch.no_grad():
        output = model(tensor)
        probabilities = torch.softmax(output, dim=1)
        confidence, prediction = torch.max(probabilities, dim=1)

    predicted_class = CLASSES[prediction.item()]
    predicted_probability = confidence.item()
    return predicted_class, predicted_probability


def show_processed_image(image_path: str | Path, image_size: int = 224) -> None:
    """Mostra a imagem apos resize e normalizacao revertida para visualizacao."""
    import matplotlib.pyplot as plt

    image = read_image_with_opencv(image_path)
    tensor = get_eval_transforms(image_size=image_size)(image)

    mean = torch.tensor(IMAGENET_MEAN).view(3, 1, 1)
    std = torch.tensor(IMAGENET_STD).view(3, 1, 1)
    image_to_show = (tensor * std + mean).clamp(0, 1).permute(1, 2, 0).numpy()

    plt.imshow(image_to_show)
    plt.axis("off")
    plt.title("Imagem processada")
    plt.show()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classifica uma imagem de vegetacao.")
    parser.add_argument("--image", required=True, help="Caminho da imagem para classificacao.")
    parser.add_argument("--model-path", default="models/modelo_vegetacao.pth", help="Caminho do modelo treinado.")
    parser.add_argument("--image-size", type=int, default=224, help="Tamanho da imagem de entrada.")
    parser.add_argument("--show-image", action="store_true", help="Mostra a imagem processada usada na inferencia.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    predicted_class, confidence = predict(args.image, args.model_path, args.image_size)

    print(f"Imagem: {Path(args.image).name}")
    print(f"Classe prevista: {predicted_class}")
    print(f"Confianca: {confidence * 100:.1f}%")

    if args.show_image:
        show_processed_image(args.image, args.image_size)


if __name__ == "__main__":
    main()
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
