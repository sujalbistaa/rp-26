"""Person re-ID baseline on Market-1501, with a clean train/test separation.

Replaces the prototype's training.py. What changed, and why:
  - Train only on bounding_box_train. Test on query vs bounding_box_test, which hold different
    people. The prototype's three folders held the same files, so its 100% Rank-1 meant nothing.
  - Standard Market-1501 protocol: drop gallery images with the same person AND camera as the
    query, and drop junk (id -1). Distractors (id 0000) stay in the gallery.
  - ResNet-50, last stride 1, BNNeck, label-smoothed ID loss + batch-hard triplet, random
    erasing, warm-up and step decay, for 120 epochs rather than 2 or 3.

Only standard Market-1501 filenames are loaded. Anything else is counted and left out.

    python train_reid.py --data ../resources/Market-1501 --out runs/main
"""

import argparse
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models
import torchvision.transforms as T
from PIL import Image
from torch.utils.data import DataLoader, Dataset, Sampler

NAME = re.compile(r"^(-?\d+)_c(\d)s\d_\d+_\d+\.jpg$")
SIZE = (256, 128)
MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]


def scan(folder: Path) -> tuple[list[tuple[str, int, int]], list[str]]:
    items, rejected = [], []
    for path in sorted(folder.rglob("*.jpg")):
        match = NAME.match(path.name)
        if match:
            items.append((str(path), int(match[1]), int(match[2])))
        else:
            rejected.append(str(path.parent.name))
    return items, rejected


class Images(Dataset):
    def __init__(self, items, transform, labels=None):
        self.items, self.transform, self.labels = items, transform, labels

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, pid, cam = self.items[i]
        image = self.transform(Image.open(path).convert("RGB"))
        return image, (self.labels[pid] if self.labels else pid), cam


class IdentitySampler(Sampler):
    """P identities x K images per batch, so batch-hard triplet always has positives."""

    def __init__(self, items, labels, p, k):
        self.p, self.k = p, k
        self.by_id = defaultdict(list)
        for i, (_, pid, _) in enumerate(items):
            self.by_id[labels[pid]].append(i)
        self.ids = list(self.by_id)
        self.batches = len(items) // (p * k)

    def __len__(self):
        return self.batches * self.p * self.k

    def __iter__(self):
        for _ in range(self.batches):
            for pid in random.sample(self.ids, self.p):
                pool = self.by_id[pid]
                picks = random.sample(pool, self.k) if len(pool) >= self.k else random.choices(pool, k=self.k)
                yield from picks


class Net(nn.Module):
    def __init__(self, classes):
        super().__init__()
        base = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V2)
        base.layer4[0].conv2.stride = (1, 1)
        base.layer4[0].downsample[0].stride = (1, 1)
        self.backbone = nn.Sequential(*list(base.children())[:-2])
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.bn = nn.BatchNorm1d(2048)
        self.bn.bias.requires_grad_(False)
        self.head = nn.Linear(2048, classes, bias=False)
        nn.init.normal_(self.head.weight, std=0.001)

    def forward(self, x):
        feat = self.pool(self.backbone(x)).flatten(1)
        neck = self.bn(feat)
        return (feat, self.head(neck)) if self.training else neck


def triplet_loss(feat, labels, margin=0.3):
    dist = torch.cdist(feat, feat)
    same = labels[:, None] == labels[None, :]
    hardest_pos = (dist * same).max(1).values
    hardest_neg = dist.masked_fill(same, float("inf")).min(1).values
    return F.relu(hardest_pos - hardest_neg + margin).mean()


def smooth_ce(logits, labels, eps=0.1):
    logp = F.log_softmax(logits, 1)
    return -((1 - eps) * logp.gather(1, labels[:, None]).squeeze(1) + eps * logp.mean(1)).mean()


@torch.no_grad()
def features(model, items, transform, device, workers):
    model.eval()
    loader = DataLoader(Images(items, transform), batch_size=128, num_workers=workers, pin_memory=True)
    out = []
    for images, _, _ in loader:
        images = images.to(device)
        with torch.autocast("cuda", dtype=torch.float16):
            feat = model(images) + model(images.flip(3))
        out.append(F.normalize(feat.float(), dim=1).cpu())
    return torch.cat(out)


def evaluate(q_feat, g_feat, query, gallery, device):
    """Rank-k CMC and mAP, standard protocol."""
    q_pid = np.array([i[1] for i in query])
    q_cam = np.array([i[2] for i in query])
    g_pid = np.array([i[1] for i in gallery])
    g_cam = np.array([i[2] for i in gallery])
    cmc = np.zeros(g_pid.size)
    aps, valid = [], 0
    g_dev = g_feat.to(device)
    for start in range(0, len(query), 256):
        order = torch.argsort(q_feat[start : start + 256].to(device) @ g_dev.T, dim=1, descending=True).cpu().numpy()
        for row, i in zip(order, range(start, start + order.shape[0]), strict=True):
            keep = ~(((g_pid[row] == q_pid[i]) & (g_cam[row] == q_cam[i])) | (g_pid[row] == -1))
            hits = (g_pid[row][keep] == q_pid[i]).astype(np.float64)
            if not hits.any():
                continue
            valid += 1
            first = int(hits.argmax())
            cmc[first:] += 1
            precision = hits.cumsum() / (np.arange(hits.size) + 1)
            aps.append(float((precision * hits).sum() / hits.sum()))
    cmc /= valid
    return {
        "rank1": 100 * cmc[0],
        "rank5": 100 * cmc[4],
        "rank10": 100 * cmc[9],
        "mAP": 100 * float(np.mean(aps)),
        "queries": valid,
    }


def learning_rate(epoch, base):
    if epoch <= 10:
        return base * epoch / 10
    return base * (1 if epoch <= 40 else 0.1 if epoch <= 70 else 0.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--eval-every", type=int, default=20)
    parser.add_argument("--p", type=int, default=16)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--lr", type=float, default=3.5e-4)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.benchmark = True
    device = torch.device("cuda")
    args.out.mkdir(parents=True, exist_ok=True)

    train, bad_train = scan(args.data / "bounding_box_train")
    query, bad_query = scan(args.data / "query")
    gallery, bad_gallery = scan(args.data / "bounding_box_test")
    train = [i for i in train if i[1] > 0]
    train_ids, test_ids = {i[1] for i in train}, {i[1] for i in query}
    overlap = train_ids & test_ids
    data_note = {
        "train_images": len(train),
        "train_ids": len(train_ids),
        "query_images": len(query),
        "query_ids": len(test_ids),
        "gallery_images": len(gallery),
        "rejected_nonstandard_names": {
            "train": len(bad_train),
            "query": len(bad_query),
            "gallery": len(bad_gallery),
        },
        "train_test_id_overlap": len(overlap),
    }
    print(json.dumps(data_note, indent=1), flush=True)
    if overlap:
        raise SystemExit(f"train and test share {len(overlap)} identities, refusing to run")

    labels = {pid: n for n, pid in enumerate(sorted(train_ids))}
    train_tf = T.Compose([
        T.Resize(SIZE, interpolation=T.InterpolationMode.BICUBIC),
        T.RandomHorizontalFlip(),
        T.Pad(10),
        T.RandomCrop(SIZE),
        T.ToTensor(),
        T.Normalize(MEAN, STD),
        T.RandomErasing(p=0.5, scale=(0.02, 0.4), value=MEAN),
    ])
    test_tf = T.Compose([
        T.Resize(SIZE, interpolation=T.InterpolationMode.BICUBIC),
        T.ToTensor(),
        T.Normalize(MEAN, STD),
    ])

    loader = DataLoader(
        Images(train, train_tf, labels),
        batch_size=args.p * args.k,
        sampler=IdentitySampler(train, labels, args.p, args.k),
        num_workers=args.workers,
        pin_memory=True,
        persistent_workers=True,
        drop_last=True,
    )
    model = Net(len(labels)).to(device)
    optimiser = torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=5e-4)
    scaler = torch.amp.GradScaler("cuda")

    log = {"data": data_note, "args": {k: str(v) for k, v in vars(args).items()}, "epochs": [], "evals": []}
    best = -1.0
    started = time.time()
    for epoch in range(1, args.epochs + 1):
        for group in optimiser.param_groups:
            group["lr"] = learning_rate(epoch, args.lr)
        model.train()
        total, seen = 0.0, 0
        for images, targets, _ in loader:
            images, targets = images.to(device, non_blocking=True), targets.to(device, non_blocking=True)
            optimiser.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                feat, logits = model(images)
            loss = smooth_ce(logits.float(), targets) + triplet_loss(feat.float(), targets)
            scaler.scale(loss).backward()
            scaler.step(optimiser)
            scaler.update()
            total += float(loss)
            seen += 1
        entry = {"epoch": epoch, "loss": total / seen, "minutes": (time.time() - started) / 60}
        log["epochs"].append(entry)
        print(f"epoch {epoch} loss {entry['loss']:.4f} elapsed {entry['minutes']:.1f} min", flush=True)

        if epoch % args.eval_every == 0 or epoch == args.epochs:
            q = features(model, query, test_tf, device, args.workers)
            g = features(model, gallery, test_tf, device, args.workers)
            scores = {"epoch": epoch, **evaluate(q, g, query, gallery, device)}
            log["evals"].append(scores)
            print(json.dumps(scores), flush=True)
            if scores["mAP"] > best:
                best = scores["mAP"]
                torch.save(model.state_dict(), args.out / "best.pth")
        (args.out / "log.json").write_text(json.dumps(log, indent=1))
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
