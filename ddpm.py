import os
import torch
import torch.nn as nn
from matplotlib import pyplot as plt
from tqdm import tqdm
from torch import optim
from utils import *
from Model import UNet
import logging
from torch.utils.tensorboard import SummaryWriter
import torch.nn.functional as F
from diffusers import DDPMScheduler

logging.basicConfig(format="%(asctime)s - %(levelname)s: %(message)s", level=logging.INFO, datefmt="%I:%M:%S")

class Diffusion:
    def __init__(self, noise_steps=1000, beta_start=1e-4, beta_end=0.02, img_size=64, device="cuda"):
        self.noise_steps = noise_steps
        self.beta_start = beta_start
        self.beta_end = beta_end
        self.img_size = img_size
        self.device = device
        self.beta = self.prepare_noise_schedule().to(device)
        self.alpha = 1. - self.beta
        self.alpha_hat = torch.cumprod(self.alpha, dim=0)

    def prepare_noise_schedule(self):
        return torch.linspace(self.beta_start, self.beta_end, self.noise_steps)

    def noise_images(self, x, t):
        sqrt_alpha_hat = torch.sqrt(self.alpha_hat[t])[:, None, None, None]
        sqrt_one_minus_alpha_hat = torch.sqrt(1 - self.alpha_hat[t])[:, None, None, None]
        Ɛ = torch.randn_like(x)
        return sqrt_alpha_hat * x + sqrt_one_minus_alpha_hat * Ɛ, Ɛ

    def sample_timesteps(self, n):
        return torch.randint(low=1, high=self.noise_steps, size=(n,))

    def sample(self, model, scheduler, n):
        logging.info(f"Sampling {n} new images....")
        model.eval()
        with torch.no_grad():
            x = torch.randn((n, 1, self.img_size, self.img_size)).to(self.device)
            for i, t in enumerate(tqdm(scheduler.timesteps)):
                t = t.repeat(n).to(self.device)
                predicted_noise = model(x, t)
                x = scheduler.step(predicted_noise, t[0], x).prev_sample
        model.train()
        x = (x.clamp(-1, 1) + 1) / 2
        x = (x * 255).type(torch.uint8)
        return x


best_train_loss = float('inf')


def train(args):
    setup_logging(args.run_name)
    device = args.device
    dataloader = get_data(args)
    model = UNet(T=1000, ch=128, ch_mult=[1, 2, 2, 2], attn=[1],
                 num_res_blocks=2, dropout=0.01).to(device)
    optimizer = optim.AdamW(model.parameters(), lr=args.lr)
    mse = nn.MSELoss()
    diffusion = Diffusion(img_size=args.image_size, device=device)
    logger = SummaryWriter(os.path.join("runs", args.run_name))
    l = len(dataloader)
    scheduler = DDPMScheduler()

    for epoch in range(args.epochs):
        logging.info(f"Starting epoch {epoch}:")
        pbar = tqdm(dataloader)
        for i, (images, _) in enumerate(pbar):
            images = images.to(device)
            t = diffusion.sample_timesteps(images.shape[0]).to(device)
            x_t, noise = diffusion.noise_images(images, t)
            predicted_noise = model(x_t, t)
            loss = mse(noise, predicted_noise)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            pbar.set_postfix(MSE=loss.item())
            logger.add_scalar("MSE", loss.item(), global_step=epoch * l + i)
        if epoch > 5000 and epoch % 400 == 0:
            sampled_images = diffusion.sample(model, scheduler, n=images.shape[0])
            save_images(sampled_images, os.path.join("results", args.run_name, f"{epoch}.jpg"))
            torch.save(model.state_dict(), os.path.join("models", args.run_name, f"ckpt{epoch}.pt"))


def launch():
    import argparse
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    args.run_name = f"label{label}"
    args.epochs = 10000
    args.batch_size = 5
    args.image_size = 64
    args.dataset_path = f"./database/origin/label{label}"
    args.device = "cuda"
    args.lr = 1e-4
    train(args)


if __name__ == '__main__':
    label = 1
    launch()
    device = "cuda"
    model = UNet(T=1000, ch=128, ch_mult=[1, 2, 2, 2], attn=[1],
                 num_res_blocks=2, dropout=0.1).to(device)
    ckpt = torch.load(f"./models/ckpt9600.pt")
    model.load_state_dict(ckpt)
    diffusion = Diffusion(img_size=64, device=device)
    scheduler = DDPMScheduler()
    sampled_images = diffusion.sample(model, scheduler, 360)
    output_dir = f'./database/output/label{label}'
    for i, image in enumerate(sampled_images.cpu()):
        pil_image = Image.fromarray(image.squeeze().numpy())
        save_path = os.path.join(output_dir, f'image_{i + 1}.png')
        pil_image.save(save_path)