''' This is from this Fed-BioMed tutorial, but locally, without FL
https://gitlab.inria.fr/fedbiomed/fedbiomed/-/blob/develop/notebooks/monai-2d-image-classification-gpu.ipynb
https://github.com/Project-MONAI/tutorials/blob/main/2d_classification/mednist_tutorial.ipynb'''

import os
import shutil
import tempfile
import matplotlib.pyplot as plt
import PIL
import torch
import numpy as np
from sklearn.metrics import classification_report

from monai.apps import download_and_extract
from monai.config import print_config
from monai.data import decollate_batch, DataLoader
from monai.metrics import ROCAUCMetric
from monai.networks.nets import DenseNet121
from monai.transforms import (
    Activations,
    EnsureChannelFirst,
    AsDiscrete,
    Compose,
    LoadImage,
    RandFlip,
    RandRotate,
    RandZoom,
    ScaleIntensity,
)
from monai.utils import set_determinism
from adni_dataset import ADNIDataset_bl
from glob import glob
import pandas as pd

# print_config()

## Setup data dir
directory = '/home/imber/Projects/PASSIAN/data/ADNI/aramis_preproc/CAPS_smallsample'  # os.environ.get("MONAI_DATA_DIRECTORY")
root_dir = tempfile.mkdtemp() if directory is None else directory
print(root_dir)

## DL dataset
# resource = "https://github.com/Project-MONAI/MONAI-extra-test-data/releases/download/0.8.1/MedNIST.tar.gz"
# md5 = "0bc7306e7427e00ad1c5526a6677552d"
#
# compressed_file = os.path.join(root_dir, "MedNIST.tar.gz")
# data_dir = os.path.join(root_dir, "MedNIST")
# if not os.path.exists(data_dir):
#     download_and_extract(resource, compressed_file, root_dir, md5)
data_dir = directory

## Set deterministic
set_determinism(seed=17)

## Read img filenames
# class_names = sorted(x for x in os.listdir(data_dir)
#                      if os.path.isdir(os.path.join(data_dir, x)))
# num_class = len(class_names)
# image_files = [
#     [
#         os.path.join(data_dir, class_names[i], x)
#         for x in os.listdir(os.path.join(data_dir, class_names[i]))
#     ]
#     for i in range(num_class)
# ]
# num_each = [len(image_files[i]) for i in range(num_class)]
# image_files_list = []
# image_class = []
# for i in range(num_class):
#     image_files_list.extend(image_files[i])
#     image_class.extend([i] * num_each[i])
# num_total = len(image_class)
# image_width, image_height = PIL.Image.open(image_files_list[0]).size
#
# print(f"Total image count: {num_total}")
# print(f"Image dimensions: {image_width} x {image_height}")
# print(f"Label names: {class_names}")
# print(f"Label counts: {num_each}")

## Visualize
# plt.subplots(3, 3, figsize=(8, 8))
# for i, k in enumerate(np.random.randint(num_total, size=9)):
#     im = PIL.Image.open(image_files_list[k])
#     arr = np.array(im)
#     plt.subplot(3, 3, i + 1)
#     plt.xlabel(class_names[image_class[k]])
#     plt.imshow(arr, cmap="gray", vmin=0, vmax=255)
# plt.tight_layout()
# plt.show()

# Notes: This is the CAPS preprocd dir from aramis clinidaDL; can later change ses-bl to include more t-pts
image_files_list = sorted(glob(os.path.join(data_dir, 'subjects/*/ses-bl/t1_linear/*Crop*.nii.gz')))

## Prep train, val, test
val_frac = 0.2
test_frac = 0.1
length = len(image_files_list)
num_class = 3
indices = np.arange(length)
np.random.shuffle(indices)

test_split = int(test_frac * length)
val_split = int(val_frac * length) + test_split
test_indices = indices[:test_split]
val_indices = indices[test_split:val_split]
train_indices = indices[val_split:]

labels_csv = "/home/imber/Projects/PASSIAN/data/ADNI/aramis_preproc/CAPS_smallsample/ADNIMERGE_2022-09-02.csv"
labels_df = pd.read_csv(labels_csv, low_memory=False)[['PTID', 'DX', 'VISCODE']]  # only ids, dx, visit
labels_df['FID'] = 'sub-' + labels_df['PTID'].str.replace('_', '')  # add a new file ID columns
labels_df = labels_df[labels_df['VISCODE'] == 'bl']
labels_df = labels_df[labels_df['FID'].isin([file.split('/')[10] for file in image_files_list])]  # filter
labels_df = labels_df.sort_values('FID')  # this should work - test on larger sample!
## todo: remap this to numbers (1, 2 , 3 ; check whether it works before converting to tensor
classdict = {'CN': 1.0, 'MCI': 2.0, 'Dementia': 3.0}
labels_df = labels_df.replace({'DX': classdict})
labels = torch.tensor(list(labels_df['DX']))
labels = labels.type(torch.LongTensor)

train_x = [image_files_list[i] for i in train_indices]
train_y = [labels[i] for i in train_indices]
val_x = [image_files_list[i] for i in val_indices]
val_y = [labels[i] for i in val_indices]
test_x = [image_files_list[i] for i in test_indices]
test_y = [labels[i] for i in test_indices]

print(
    f"Training count: {len(train_x)}, Validation count: "
    f"{len(val_x)}, Test count: {len(test_x)}")

## Define train transforms (augmentations)
train_transforms = Compose(
    [
        LoadImage(image_only=True),
        EnsureChannelFirst(),
        ScaleIntensity(),
        RandRotate(range_x=np.pi / 12, prob=0.5, keep_size=True),
        RandFlip(spatial_axis=0, prob=0.5),
        RandZoom(min_zoom=0.9, max_zoom=1.1, prob=0.5),
    ]
)

val_transforms = Compose(
    [LoadImage(image_only=True), EnsureChannelFirst(), ScaleIntensity()])

y_pred_trans = Compose([Activations(softmax=True)])
y_trans = Compose([AsDiscrete(to_onehot=num_class)])

## Define dataset and dataloader
train_ds = ADNIDataset_bl(train_x, train_y, train_transforms)
train_loader = DataLoader(
    train_ds, batch_size=3, shuffle=True, num_workers=10)

val_ds = ADNIDataset_bl(val_x, val_y, val_transforms)
val_loader = DataLoader(
    val_ds, batch_size=3, num_workers=10)

test_ds = ADNIDataset_bl(test_x, test_y, val_transforms)
test_loader = DataLoader(
    test_ds, batch_size=3, num_workers=10)

## Define network and optimiser
# note, random transforms each epoch means different data each epoch
device = torch.device("cuda:1" if torch.cuda.is_available() else "cpu")
model = DenseNet121(spatial_dims=3, in_channels=1,
                    out_channels=num_class).to(device)
loss_function = torch.nn.CrossEntropyLoss()
optimizer = torch.optim.Adam(model.parameters(), 1e-5)
max_epochs = 4
val_interval = 1
auc_metric = ROCAUCMetric()

## Train model
best_metric = -1
best_metric_epoch = -1
epoch_loss_values = []
metric_values = []

for epoch in range(max_epochs):
    print("-" * 10)
    print(f"epoch {epoch + 1}/{max_epochs}")
    model.train()
    epoch_loss = 0
    step = 0
    for batch_data in train_loader:
        step += 1
        inputs, labels = batch_data[0].to(device), batch_data[1].to(device)
        optimizer.zero_grad()
        outputs = model(inputs)
        loss = loss_function(outputs, labels)
        loss.backward()
        optimizer.step()
        epoch_loss += loss.item()
        print(
            f"{step}/{len(train_ds) // train_loader.batch_size}, "
            f"train_loss: {loss.item():.4f}")
        epoch_len = len(train_ds) // train_loader.batch_size
    epoch_loss /= step
    epoch_loss_values.append(epoch_loss)
    print(f"epoch {epoch + 1} average loss: {epoch_loss:.4f}")

    if (epoch + 1) % val_interval == 0:
        model.eval()
        with torch.no_grad():
            y_pred = torch.tensor([], dtype=torch.float32, device=device)
            y = torch.tensor([], dtype=torch.long, device=device)
            for val_data in val_loader:
                val_images, val_labels = (
                    val_data[0].to(device),
                    val_data[1].to(device),
                )
                y_pred = torch.cat([y_pred, model(val_images)], dim=0)
                y = torch.cat([y, val_labels], dim=0)
            y_onehot = [y_trans(i) for i in decollate_batch(y, detach=False)]
            y_pred_act = [y_pred_trans(i) for i in decollate_batch(y_pred)]
            auc_metric(y_pred_act, y_onehot)
            result = auc_metric.aggregate()
            auc_metric.reset()
            del y_pred_act, y_onehot
            metric_values.append(result)
            acc_value = torch.eq(y_pred.argmax(dim=1), y)
            acc_metric = acc_value.sum().item() / len(acc_value)
            if result > best_metric:
                best_metric = result
                best_metric_epoch = epoch + 1
                torch.save(model.state_dict(), os.path.join(
                    root_dir, "best_metric_model.pth"))
                print("saved new best metric model")
            print(
                f"current epoch: {epoch + 1} current AUC: {result:.4f}"
                f" current accuracy: {acc_metric:.4f}"
                f" best AUC: {best_metric:.4f}"
                f" at epoch: {best_metric_epoch}"
            )

print(
    f"train completed, best_metric: {best_metric:.4f} "
    f"at epoch: {best_metric_epoch}")

## Plot model and metric
plt.figure("train", (12, 6))
plt.subplot(1, 2, 1)
plt.title("Epoch Average Loss")
x = [i + 1 for i in range(len(epoch_loss_values))]
y = epoch_loss_values
plt.xlabel("epoch")
plt.plot(x, y)
plt.subplot(1, 2, 2)
plt.title("Val AUC")
x = [val_interval * (i + 1) for i in range(len(metric_values))]
y = metric_values
plt.xlabel("epoch")
plt.plot(x, y)
plt.show()

# ## Evaluate model on test dataset
# model.load_state_dict(torch.load(
#     os.path.join(root_dir, "best_metric_model.pth")))
# model.eval()
# y_true = []
# y_pred = []
# with torch.no_grad():
#     for test_data in test_loader:
#         test_images, test_labels = (
#             test_data[0].to(device),
#             test_data[1].to(device),
#         )
#         pred = model(test_images).argmax(dim=1)
#         for i in range(len(pred)):
#             y_true.append(test_labels[i].item())
#             y_pred.append(pred[i].item())
#
# ## Print classification report
# print(classification_report(
#     y_true, y_pred, target_names=class_names, digits=4))





