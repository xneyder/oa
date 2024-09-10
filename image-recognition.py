import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from PIL import Image
import requests
from io import BytesIO

# Force CPU execution
device = "cpu"

# url = "https://huggingface.co/qresearch/llama-3-vision-alpha-hf/resolve/main/assets/demo-2.jpg"
# response = requests.get(url)
# image = Image.open(BytesIO(response.content))
# Load the image from the local file 'image.jpg'
image_path = "image.jpg"
image = Image.open(image_path)

print("Image size:", image.size)

model = AutoModelForCausalLM.from_pretrained(
    "qresearch/llama-3.1-8B-vision-378",
    trust_remote_code=True,
    torch_dtype=torch.float16,
).to(device)

print("Model loaded")

tokenizer = AutoTokenizer.from_pretrained("qresearch/llama-3.1-8B-vision-378", use_fast=True)

print("Tokenizer loaded")

print(
    model.answer_question(
        image, "tell me from the image if i apply all the the discounts on the prodyct and i am expenting more than 100 dolars and more than 10 units what will be the final price per unit of the product", tokenizer, max_new_tokens=128, do_sample=True, temperature=0.3
    ),
)
