"""Predição de uma imagem individual."""

import argparse
from pathlib import Path

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
