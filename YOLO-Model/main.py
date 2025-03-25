#############################################
# main.py
#############################################
import torch
import json
import time
import sys
import os
import random
import torch.optim as optim
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import confusion_matrix
from tqdm import tqdm
from torch.utils.data import DataLoader
from dataset_wideband_yolo import WidebandYoloDataset
from model_and_loss_wideband_yolo import WidebandYoloModel, WidebandYoloLoss
from config_wideband_yolo import (
    NUM_CLASSES,
    CREATE_NEW_DATASET,
    BATCH_SIZE,
    EPOCHS,
    LEARNING_RATE,
    VAL_PRINT_SAMPLES,
    PRINT_CONFIG_FILE,
    print_config_file
)
from utils import create_dataset

with open("./configs/system_parameters.json") as f:
    system_parameters = json.load(f)

working_directory = system_parameters["Working_Directory"]
sys.path.append(working_directory)

rng_seed = system_parameters["Random_Seed"]
data_dir = system_parameters["Dataset_Directory"]

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

torch.manual_seed(rng_seed)
random.seed(rng_seed)

if torch.cuda.is_available():
    print("\n\nCUDA is available.")
    print(f"CUDA version: {torch.version.cuda}")
    print(f"Available devices: {torch.cuda.device_count()}")
    print(f"Current device: {torch.cuda.current_device()}")
    print(f"Device name: {torch.cuda.get_device_name(0)}")
else:
    print("\n\nCUDA is not available. Using CPU.")
print("\n")

def main():
    # Optionally create the dataset
    if CREATE_NEW_DATASET:
        create_dataset(data_dir, rng_seed)
        
    # Print the configuration file
    if PRINT_CONFIG_FILE:
        print_config_file()

    # 1) Build dataset and loaders
    train_dataset = WidebandYoloDataset(
        os.path.join(data_dir, "training"), transform=None
    )
    val_dataset = WidebandYoloDataset(
        os.path.join(data_dir, "validation"), transform=None
    )
    test_dataset = WidebandYoloDataset(
        os.path.join(data_dir, "testing"), transform=None
    )
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    val_loader   = DataLoader(val_dataset,   batch_size=BATCH_SIZE, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)

    # 2) Create model & loss
    num_samples = train_dataset.get_num_samples()
    model = WidebandYoloModel(num_samples).to(device)
    criterion = WidebandYoloLoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)

    # 3) Training loop
    for epoch in range(EPOCHS):
        # Training
        model, avg_train_loss, train_mean_freq_err, train_cls_accuracy = train_model(
            model, train_loader, device, optimizer, criterion, epoch)

        # Validation
        avg_val_loss, val_mean_freq_err, val_cls_accuracy, val_frames = validate_model(
            model, val_loader, device, criterion, epoch)

        # Print metrics for this epoch
        print(f"Epoch [{epoch+1}/{EPOCHS}]")
        print(f"  Train: Loss={avg_train_loss:.4f},"
              f"  MeanFreqErr={train_mean_freq_err:.4f},"
              f"  ClsAcc={train_cls_accuracy:.2f}%")
        print(f"  Valid: Loss={avg_val_loss:.4f},"
              f"  MeanFreqErr={val_mean_freq_err:.4f},"
              f"  ClsAcc={val_cls_accuracy:.2f}%")

        # Print a random subset of "frames"
        random.shuffle(val_frames)
        to_print = val_frames[:VAL_PRINT_SAMPLES]  # up to VAL_PRINT_SAMPLES frames
        print(f"\n  Some random frames from validation (only {VAL_PRINT_SAMPLES} shown):")
        print(f"  Prediction format: (freq, class, conf)")
        print(f"  GroundTruth format: (freq, class)")
        for idx, frame_dict in enumerate(to_print, 1):
            pred_list = frame_dict["pred_list"]
            gt_list   = frame_dict["gt_list"]

            print(f"    Frame {idx}:")
            print(f"      Predicted => {pred_list}")
            print(f"      GroundTruth=> {gt_list}")
        print("")

    print("Training complete.")
    
    # 4) Test the model
    test_model(model, test_loader, device)

def train_model(model, train_loader, device, optimizer, criterion, epoch):
    model.train()
    total_train_loss = 0.0

    # For metrics:
    train_obj_count = 0
    train_correct_cls = 0
    train_sum_freq_err = 0.0

    for iq_tensor, label_tensor, _ in tqdm(train_loader, desc=f"Training epoch {epoch+1}/{EPOCHS}"):
        iq_tensor   = iq_tensor.to(device)
        label_tensor= label_tensor.to(device)

        optimizer.zero_grad()
        pred = model(iq_tensor)
        loss = criterion(pred, label_tensor)
        loss.backward()
        optimizer.step()
        total_train_loss += loss.item()

        # Additional training metrics
        bsize = pred.shape[0]
        pred_reshape = pred.view(bsize, pred.shape[1], -1, (1 + 1 + NUM_CLASSES))
        x_pred     = pred_reshape[..., 0]
        conf_pred  = pred_reshape[..., 1]
        class_pred = pred_reshape[..., 2:]

        x_tgt      = label_tensor[..., 0]
        conf_tgt   = label_tensor[..., 1]
        class_tgt  = label_tensor[..., 2:]

        obj_mask = (conf_tgt > 0)
        freq_err = torch.abs(x_pred - x_tgt)

        pred_class_idx = torch.argmax(class_pred, dim=-1)
        true_class_idx = torch.argmax(class_tgt, dim=-1)

        batch_obj_count   = obj_mask.sum().item()
        batch_sum_freq_err= freq_err[obj_mask].sum().item()
        correct_cls_mask  = (pred_class_idx == true_class_idx)
        batch_correct_cls = correct_cls_mask[obj_mask].sum().item()

        train_obj_count     += batch_obj_count
        train_sum_freq_err  += batch_sum_freq_err
        train_correct_cls   += batch_correct_cls

    avg_train_loss = total_train_loss / len(train_loader)
    if train_obj_count > 0:
        train_mean_freq_err = train_sum_freq_err / train_obj_count
        train_cls_accuracy  = 100.0 * (train_correct_cls / train_obj_count)
    else:
        train_mean_freq_err = 0.0
        train_cls_accuracy  = 0.0
        
    return model, avg_train_loss, train_mean_freq_err, train_cls_accuracy

def validate_model(model, val_loader, device, criterion, epoch):
    model.eval()
    total_val_loss = 0.0

    val_obj_count = 0
    val_correct_cls = 0
    val_sum_freq_err = 0.0

    val_frames = []

    with torch.no_grad():
        for iq_tensor, label_tensor, _ in tqdm(val_loader, desc=f"Validation epoch {epoch+1}/{EPOCHS}"):
            iq_tensor   = iq_tensor.to(device)
            label_tensor= label_tensor.to(device)

            pred = model(iq_tensor)
            loss = criterion(pred, label_tensor)
            total_val_loss += loss.item()

            bsize = pred.shape[0]
            pred_reshape = pred.view(bsize, pred.shape[1], -1, (1 + 1 + NUM_CLASSES))

            x_pred     = pred_reshape[..., 0]
            conf_pred  = pred_reshape[..., 1]
            class_pred = pred_reshape[..., 2:]

            x_tgt      = label_tensor[..., 0]
            conf_tgt   = label_tensor[..., 1]
            class_tgt  = label_tensor[..., 2:]

            obj_mask = (conf_tgt > 0)
            freq_err = torch.abs(x_pred - x_tgt)

            pred_class_idx = torch.argmax(class_pred, dim=-1)
            true_class_idx = torch.argmax(class_tgt, dim=-1)

            # For metric sums
            batch_obj_count   = obj_mask.sum().item()
            batch_sum_freq_err= freq_err[obj_mask].sum().item()
            correct_cls_mask  = (pred_class_idx == true_class_idx)
            batch_correct_cls = correct_cls_mask[obj_mask].sum().item()

            val_obj_count     += batch_obj_count
            val_sum_freq_err  += batch_sum_freq_err
            val_correct_cls   += batch_correct_cls

            # For printing the results in a "frame" manner:
            # We group each sample in this batch separately.
            for i in range(bsize):
                pred_list = []
                gt_list   = []

                for s_idx in range(pred_reshape.shape[1]):
                    for b_idx in range(pred_reshape.shape[2]):
                        x_p   = x_pred[i, s_idx, b_idx].item()   # raw freq
                        conf  = conf_pred[i, s_idx, b_idx].item()
                        cls_p = pred_class_idx[i, s_idx, b_idx].item()

                        if conf > 0.05:
                            pred_list.append((x_p, cls_p, conf))

                        if conf_tgt[i, s_idx, b_idx] > 0:
                            x_g = x_tgt[i, s_idx, b_idx].item()
                            cls_g= true_class_idx[i, s_idx, b_idx].item()
                            gt_list.append((x_g, cls_g))

                # sort by conf desc
                pred_list.sort(key=lambda tup: tup[2], reverse=True)

                frame_dict = {
                    "pred_list": pred_list,
                    "gt_list":   gt_list
                }
                val_frames.append(frame_dict)

    avg_val_loss = total_val_loss / len(val_loader)
    if val_obj_count > 0:
        val_mean_freq_err = val_sum_freq_err / val_obj_count
        val_cls_accuracy  = 100.0 * (val_correct_cls / val_obj_count)
    else:
        val_mean_freq_err = 0.0
        val_cls_accuracy  = 0.0
        
    return avg_val_loss, val_mean_freq_err, val_cls_accuracy, val_frames

def test_model(model, test_loader, device):
    """
    1) Test the model on the test set, gather classification accuracy, freq error, etc.
    2) Also compute these metrics per SNR
    3) Plot confusion matrix of classification
    """

    model.eval()
    total_obj_count = 0
    total_correct_cls = 0
    total_freq_err = 0.0

    # For confusion matrix
    overall_true_classes = []
    overall_pred_classes = []

    # For per-SNR stats
    snr_obj_count = {}
    snr_correct_cls = {}
    snr_freq_err = {}

    with torch.no_grad():
        for (iq_tensor, label_tensor, snr_tensor) in tqdm(test_loader, desc=f"Testing on test set"):
            iq_tensor   = iq_tensor.to(device)
            label_tensor= label_tensor.to(device)

            # forward
            pred = model(iq_tensor)  # shape [batch, S, B*(1+1+NUM_CLASSES)]

            # reshape
            bsize = pred.shape[0]
            Sdim = pred.shape[1]  # should be S
            # interpret bounding boxes
            pred_reshape = pred.view(bsize, Sdim, -1,  (1+1+NUM_CLASSES))

            x_pred     = pred_reshape[..., 0]  # [bsize, S, B]
            conf_pred  = pred_reshape[..., 1]
            class_pred = pred_reshape[..., 2:]

            x_tgt      = label_tensor[..., 0]
            conf_tgt   = label_tensor[..., 1]
            class_tgt  = label_tensor[..., 2:]

            # object mask
            obj_mask = (conf_tgt > 0)
            freq_err = torch.abs(x_pred - x_tgt)

            # predicted vs. true class => argmax
            pred_class_idx = torch.argmax(class_pred, dim=-1)  # [bsize, S, B]
            true_class_idx = torch.argmax(class_tgt, dim=-1)

            # Now we accumulate stats for each bounding box with obj_mask=1
            batch_obj_count = obj_mask.sum().item()
            batch_sum_freq_err = freq_err[obj_mask].sum().item()
            correct_cls_mask = (pred_class_idx == true_class_idx)
            batch_correct_cls = correct_cls_mask[obj_mask].sum().item()

            total_obj_count    += batch_obj_count
            total_freq_err     += batch_sum_freq_err
            total_correct_cls  += batch_correct_cls

            # For confusion matrix, we flatten the bounding boxes => 
            # we only consider those bounding boxes with obj_mask=1
            # then we gather pred_class_idx[obj_mask] and true_class_idx[obj_mask]
            # convert to CPU
            pred_class_flat = pred_class_idx[obj_mask].cpu().numpy()
            true_class_flat = true_class_idx[obj_mask].cpu().numpy()
            overall_true_classes.extend(true_class_flat.tolist())
            overall_pred_classes.extend(pred_class_flat.tolist())

            # Now do per-SNR
            # We have a single snr per "sample" => shape [bsize]
            # but we have multiple bounding boxes => we can count them all with that same SNR
            snrs = snr_tensor.numpy()  # shape [bsize]
            for i in range(bsize):
                sample_snr = snrs[i]
                # bounding boxes belonging to sample i => obj_mask[i]
                # gather # of obj_mask=1 in that sample
                sample_obj_mask = obj_mask[i]  # shape [S, B]
                sample_obj_count = sample_obj_mask.sum().item()
                if sample_obj_count > 0:
                    sample_freq_err = freq_err[i][sample_obj_mask].sum().item()
                    sample_correct_cls = correct_cls_mask[i][sample_obj_mask].sum().item()

                    if sample_snr not in snr_obj_count:
                        snr_obj_count[sample_snr] = 0
                        snr_correct_cls[sample_snr] = 0
                        snr_freq_err[sample_snr] = 0.0

                    snr_obj_count[sample_snr]   += sample_obj_count
                    snr_correct_cls[sample_snr] += sample_correct_cls
                    snr_freq_err[sample_snr]    += sample_freq_err

    # 1) Overall
    if total_obj_count > 0:
        overall_cls_acc = 100.0 * total_correct_cls / total_obj_count
        overall_freq_err= total_freq_err / total_obj_count
    else:
        overall_cls_acc = 0.0
        overall_freq_err= 0.0

    print("\n=== TEST SET RESULTS ===")
    print(f"Overall bounding boxes: {total_obj_count}")
    print(f"Classification Accuracy (overall): {overall_cls_acc:.2f}%")
    print(f"Mean Frequency Error (overall): {overall_freq_err:.4f}")

    # 2) Per-SNR
    # sort the SNR keys
    snr_keys_sorted = sorted(snr_obj_count.keys())
    for snr_val in snr_keys_sorted:
        if snr_obj_count[snr_val] > 0:
            cls_acc_snr = 100.0* snr_correct_cls[snr_val]/ snr_obj_count[snr_val]
            freq_err_snr= snr_freq_err[snr_val]/ snr_obj_count[snr_val]
        else:
            cls_acc_snr = 0.0
            freq_err_snr= 0.0
        print(f"SNR {snr_val:.1f}:  Accuracy={cls_acc_snr:.2f}%,  FreqErr={freq_err_snr:.4f}")

    # If dataset has "class_list" or "label_to_idx" you can define:
    class_list = test_loader.dataset.class_list  # or however you track the modclass
    cm = confusion_matrix(overall_true_classes, overall_pred_classes, labels=range(len(class_list)))
    cm_percent = cm.astype(float)
    for i in range(cm.shape[0]):
        row_sum = cm[i].sum()
        if row_sum > 0:
            cm_percent[i] = (cm[i]/row_sum)*100.0

    plt.figure(figsize=(8,6))
    sns.heatmap(
        cm_percent, 
        annot=True, fmt=".2f", cmap="Blues",
        xticklabels=class_list, yticklabels=class_list
    )
    plt.title("Test Set Confusion Matrix (%)")
    plt.xlabel("Predicted Class")
    plt.ylabel("True Class")
    plt.tight_layout()
    plt.savefig("test_confusion_matrix.png")
    plt.close()

    print("\nTest confusion matrix saved to test_confusion_matrix.png.\n")
    print("=== END OF TESTING ===")

if __name__ == "__main__":
    start_time = time.time()
    main()
    time_diff = time.time() - start_time
    print(
        f"\nCode Execution took {time_diff // 3600:.0f} hours, "
        f"{(time_diff % 3600) // 60:.0f} minutes, {time_diff % 60:.0f} seconds."
    )