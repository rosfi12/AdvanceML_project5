import os
import json
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import matplotlib.pyplot as plt
import collections
from collections import defaultdict
import random
import kagglehub
import shutil
import glob
import re
from tqdm import tqdm  # For progress tracking.

# Regular expressions for parsing Shakespeare text
CHARACTER_RE = re.compile(r'^  ([a-zA-Z][a-zA-Z ]*)\. (.*)')  # Matches character lines
CONT_RE = re.compile(r'^    (.*)')  # Matches continuation lines
COE_CHARACTER_RE = re.compile(r'^([a-zA-Z][a-zA-Z ]*)\. (.*)')  # Special regex for Comedy of Errors
COE_CONT_RE = re.compile(r'^(.*)')  # Continuation for Comedy of Errors


# Get current script directory
SCRIPT_DIR = os.getcwd()

# Download dataset
path = kagglehub.dataset_download("kewagbln/shakespeareonline")

# Debug: print downloaded files
print(f"Downloaded path: {path}")
print("Files in downloaded path:")
for file in glob.glob(os.path.join(path, "*")):
    print(f" - {file}")

# Set up paths relative to script location
DATA_PATH = os.path.join(SCRIPT_DIR, "shakespeare.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "processed_data")

# Create directories if they don't exist
os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Find and copy Shakespeare text file
shakespeare_file = None
for file in glob.glob(os.path.join(path, "*.txt")):
    shakespeare_file = file
    break

if shakespeare_file:
    shutil.copy2(shakespeare_file, DATA_PATH)
    print(f"Dataset saved to: {DATA_PATH}")
else:
    raise FileNotFoundError(f"Could not find Shakespeare text file in {path}")

def __txt_to_data(txt_dir, seq_length=80):
    """Parses text file in given directory into data for next-character model.

    Args:
        txt_dir: path to text file
        seq_length: length of strings in X
    """
    raw_text = ""
    with open(txt_dir,'r') as inf:
        raw_text = inf.read()
    raw_text = raw_text.replace('\n', ' ')
    raw_text = re.sub(r"   *", r' ', raw_text)
    dataX = []
    dataY = []
    for i in range(0, len(raw_text) - seq_length, 1):
        seq_in = raw_text[i:i + seq_length]
        seq_out = raw_text[i + seq_length]
        dataX.append(seq_in)
        dataY.append(seq_out)
    return dataX, dataY

def parse_data_in(data_dir, users_and_plays_path, raw=False):
    '''
    returns dictionary with keys: users, num_samples, user_data
    raw := bool representing whether to include raw text in all_data
    if raw is True, then user_data key
    removes users with no data
    '''
    with open(users_and_plays_path, 'r') as inf:
        users_and_plays = json.load(inf)
    files = os.listdir(data_dir)
    users = []
    hierarchies = []
    num_samples = []
    user_data = {}
    for f in files:
        user = f[:-4]
        passage = ''
        filename = os.path.join(data_dir, f)
        with open(filename, 'r') as inf:
            passage = inf.read()
        dataX, dataY = __txt_to_data(filename)
        if(len(dataX) > 0):
            users.append(user)
            if raw:
                user_data[user] = {'raw': passage}
            else:
                user_data[user] = {}
            user_data[user]['x'] = dataX
            user_data[user]['y'] = dataY
            hierarchies.append(users_and_plays[user])
            num_samples.append(len(dataY))
    all_data = {}
    all_data['users'] = users
    all_data['hierarchies'] = hierarchies
    all_data['num_samples'] = num_samples
    all_data['user_data'] = user_data
    return all_data

def parse_shakespeare(filepath, train_split=0.8):
    """
    Parses Shakespeare's text into training and testing datasets.
    """
    with open(filepath, "r") as file:
        raw_text = file.read()

    plays_data, _ = process_plays(raw_text)
    _, training_set, testing_set = split_train_test_data(plays_data, 1.0 - train_split)

    total_train = sum(len(lines) for lines in training_set.values())
    total_test = sum(len(lines) for lines in testing_set.values())
    print(f"Training examples: {total_train}")
    print(f"Testing examples: {total_test}")

    assert total_train > total_test, "Training set should be larger than test set"

    return training_set, testing_set

def process_plays(shakespeare_full):
    """
    Processes the Shakespeare text into individual plays and characters' dialogues.
    Handles special cases for "The Comedy of Errors".
    """
    plays = []
    slines = shakespeare_full.splitlines(True)[1:]  # Skip the first line (title/header)
    current_character = None
    comedy_of_errors = False

    for i, line in enumerate(slines):
        # Detect play titles and initialize character dictionary
        if "by William Shakespeare" in line:
            current_character = None
            characters = defaultdict(list)
            title = slines[i - 2].strip() if slines[i - 2].strip() else slines[i - 3].strip()
            comedy_of_errors = title == "THE COMEDY OF ERRORS"
            plays.append((title, characters))
            continue

        # Match character lines or continuation lines
        match = _match_character_regex(line, comedy_of_errors)
        if match:
            character, snippet = match.group(1).upper(), match.group(2)
            if not (comedy_of_errors and character.startswith("ACT ")):
                characters[character].append(snippet)
                current_character = character
        elif current_character:
            match = _match_continuation_regex(line, comedy_of_errors)
            if match:
                characters[current_character].append(match.group(1))

    # Filter out plays with insufficient dialogue data
    return [play for play in plays if len(play[1]) > 1], []

def _match_character_regex(line, comedy_of_errors=False):
    """Matches character dialogues, with special handling for 'The Comedy of Errors'."""
    return COE_CHARACTER_RE.match(line) if comedy_of_errors else CHARACTER_RE.match(line)

def _match_continuation_regex(line, comedy_of_errors=False):
    """Matches continuation lines of dialogues."""
    return COE_CONT_RE.match(line) if comedy_of_errors else CONT_RE.match(line)

def extract_play_title(lines, index):
    """
    Extracts the title of the play from the lines of the text.
    """
    for i in range(index - 1, -1, -1):
        if lines[i].strip():
            return lines[i].strip()
    return "UNKNOWN"

def detect_character_line(line, comedy_of_errors):
    """
    Matches a line of character dialogue.
    """
    return COE_CHARACTER_RE.match(line) if comedy_of_errors else CHARACTER_RE.match(line)

def detect_continuation_line(line, comedy_of_errors):
    """
    Matches a continuation line of dialogue.
    """
    return COE_CONT_RE.match(line) if comedy_of_errors else CONT_RE.match(line)

def _split_into_plays(shakespeare_full):
    """Splits the full data by play."""
    # List of tuples (play_name, dict from character to list of lines)
    plays = []
    discarded_lines = []  # Track discarded lines.
    slines = shakespeare_full.splitlines(True)[1:]

    # skip contents, the sonnets, and all's well that ends well
    author_count = 0
    start_i = 0
    for i, l in enumerate(slines):
        if 'by William Shakespeare' in l:
            author_count += 1
        if author_count == 2:
            start_i = i - 5
            break
    slines = slines[start_i:]

    current_character = None
    comedy_of_errors = False
    for i, line in enumerate(slines):
        # This marks the end of the plays in the file.
        if i > 124195 - start_i:
            break
        # This is a pretty good heuristic for detecting the start of a new play:
        if 'by William Shakespeare' in line:
            current_character = None
            characters = collections.defaultdict(list)
            # The title will be 2, 3, 4, 5, 6, or 7 lines above "by William Shakespeare".
            if slines[i - 2].strip():
                title = slines[i - 2]
            elif slines[i - 3].strip():
                title = slines[i - 3]
            elif slines[i - 4].strip():
                title = slines[i - 4]
            elif slines[i - 5].strip():
                title = slines[i - 5]
            elif slines[i - 6].strip():
                title = slines[i - 6]
            else:
                title = slines[i - 7]
            title = title.strip()

            assert title, (
                'Parsing error on line %d. Expecting title 2 or 3 lines above.' %
                i)
            comedy_of_errors = (title == 'THE COMEDY OF ERRORS')
            # Degenerate plays are removed at the end of the method.
            plays.append((title, characters))
            continue
        match = _match_character_regex(line, comedy_of_errors)
        if match:
            character, snippet = match.group(1), match.group(2)
            # Some character names are written with multiple casings, e.g., SIR_Toby
            # and SIR_TOBY. To normalize the character names, we uppercase each name.
            # Note that this was not done in the original preprocessing and is a
            # recent fix.
            character = character.upper()
            if not (comedy_of_errors and character.startswith('ACT ')):
                characters[character].append(snippet)
                current_character = character
                continue
            else:
                current_character = None
                continue
        elif current_character:
            match = _match_continuation_regex(line, comedy_of_errors)
            if match:
                if comedy_of_errors and match.group(1).startswith('<'):
                    current_character = None
                    continue
                else:
                    characters[current_character].append(match.group(1))
                    continue
        # Didn't consume the line.
        line = line.strip()
        if line and i > 2646:
            # Before 2646 are the sonnets, which we expect to discard.
            discarded_lines.append('%d:%s' % (i, line))
    # Remove degenerate "plays".
    return [play for play in plays if len(play[1]) > 1], discarded_lines


def _remove_nonalphanumerics(filename):
    return re.sub('\\W+', '_', filename)

def play_and_character(play, character):
    return _remove_nonalphanumerics((play + '_' + character).replace(' ', '_'))

def split_train_test_data(plays, test_fraction=0.2):
    """
    Splits the plays into training and testing datasets by character dialogues.
    """
    skipped_characters = 0
    all_train_examples = collections.defaultdict(list)
    all_test_examples = collections.defaultdict(list)

    def add_examples(example_dict, example_tuple_list):
        for play, character, sound_bite in example_tuple_list:
            example_dict[play_and_character(
                play, character)].append(sound_bite)

    users_and_plays = {}
    for play, characters in plays:
        curr_characters = list(characters.keys())
        for c in curr_characters:
            users_and_plays[play_and_character(play, c)] = play
        for character, sound_bites in characters.items():
            examples = [(play, character, sound_bite)
                        for sound_bite in sound_bites]
            if len(examples) <= 2:
                skipped_characters += 1
                # Skip characters with fewer than 2 lines since we need at least one
                # train and one test line.
                continue
            train_examples = examples
            if test_fraction > 0:
                num_test = max(int(len(examples) * test_fraction), 1)
                train_examples = examples[:-num_test]
                test_examples = examples[-num_test:]

                assert len(test_examples) == num_test
                assert len(train_examples) >= len(test_examples)

                add_examples(all_test_examples, test_examples)
                add_examples(all_train_examples, train_examples)

    return users_and_plays, all_train_examples, all_test_examples


def _write_data_by_character(examples, output_directory):
    """Writes a collection of data files by play & character."""
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)
    for character_name, sound_bites in examples.items():
        filename = os.path.join(output_directory, character_name + '.txt')
        with open(filename, 'w') as output:
            for sound_bite in sound_bites:
                output.write(sound_bite + '\n')

def letter_to_vec(c, n_vocab=90):
    """Converts a single character to a vector index based on the vocabulary size."""
    return ord(c) % n_vocab

def word_to_indices(word, n_vocab=90):
    """
    Converts a word or list of words into a list of indices.
    Each character is mapped to an index based on the vocabulary size.
    """
    if isinstance(word, list):  # If input is a list of words
        res = []
        for stringa in word:
            res.extend([ord(c) % n_vocab for c in stringa])  # Convert each word to indices
        return res
    else:  # If input is a single word
        return [ord(c) % n_vocab for c in word]

def process_x(raw_x_batch, seq_len, n_vocab):
    """
    Processes raw input data into padded sequences of indices.
    Ensures all sequences are of uniform length.
    """
    x_batch = [word_to_indices(word, n_vocab) for word in raw_x_batch]
    x_batch = [x[:seq_len] + [0] * (seq_len - len(x)) for x in x_batch]
    return torch.tensor(x_batch, dtype=torch.long)

def process_y(raw_y_batch, seq_len, n_vocab):
    """
    Processes raw target data into padded sequences of indices.
    Shifts the sequence by one character to the right.
    y[1:seq_len + 1] takes the input data, right shift of an
    element and uses the next element of the sequence to fill
    and at the end (with [0]) final padding (zeros) are (eventually)
    added to reach the desired sequence length.
    """
    y_batch = [word_to_indices(word, n_vocab) for word in raw_y_batch]
    y_batch = [y[1:seq_len + 1] + [0] * (seq_len - len(y[1:seq_len + 1])) for y in y_batch]  # Shifting and final padding
    return torch.tensor(y_batch, dtype=torch.long)

def create_batches(data, batch_size, seq_len, n_vocab):
    """
    Creates batches of input and target data from dialogues.
    Each batch contains sequences of uniform length.
    """
    x_batches = []
    y_batches = []
    dialogues = list(data.values())
    random.shuffle(dialogues)  # Shuffle to ensure randomness in batches

    batch = []
    for dialogue in dialogues:
        batch.append(dialogue)
        if len(batch) == batch_size:
            x_batch = process_x(batch, seq_len, n_vocab)
            y_batch = process_y(batch, seq_len, n_vocab)
            x_batches.append(x_batch)
            y_batches.append(y_batch)
            batch = []

    # Add the last batch if it's not full
    if batch:
        x_batch = process_x(batch, seq_len, n_vocab)
        y_batch = process_y(batch, seq_len, n_vocab)
        x_batches.append(x_batch)
        y_batches.append(y_batch)

    return x_batches, y_batches

def save_results(model, optimizer, subfolder, epoch, lr, wd, results):
            """Salva il risultato del modello e rimuove quello precedente."""
            subfolder_path = os.path.join(OUTPUT_DIR, subfolder)
            os.makedirs(subfolder_path, exist_ok=True)

            # File corrente e precedente
            filename = f"model_epoch_{epoch}_params_LR{lr}_WD{wd}.pth"
            filepath = os.path.join(subfolder_path, filename)
            filename_json = f"model_epoch_{epoch}_params_LR{lr}_WD{wd}.json"
            filepath_json = os.path.join(subfolder_path, filename_json)


            previous_filename = f"model_epoch_{epoch -1}_params_LR{lr}_WD{wd}.pth"
            previous_filepath = os.path.join(subfolder_path, previous_filename)
            previous_filename_json = f"model_epoch_{epoch -1}_params_LR{lr}_WD{wd}.json"
            previous_filepath_json = os.path.join(subfolder_path, previous_filename_json)

            # Rimuove il checkpoint precedente
            if epoch > 1 and os.path.exists(previous_filepath) and os.path.exists(previous_filepath_json):
                os.remove(previous_filepath)
                os.remove(previous_filepath_json)

            # Salva il nuovo checkpoint
            if optimizer is not None:
                torch.save({
                    'model_state_dict': model.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(),  # Salvataggio dello stato dell'ottimizzatore
                    'epoch': epoch
                }, filepath)
            else:
                torch.save({
                    'model_state_dict': model.state_dict(),
                    'epoch': epoch
                }, filepath)


            with open(filepath_json, 'w') as json_file:
                json.dump(results, json_file, indent=4)

def plot_results(validation_losses, validation_accuracies, lr, wd):
    # Plot centralized validation performance
    plt.figure(figsize=(12,10))
    # Plot Validation Loss
    plt.subplot(2, 2, 1)
    plt.plot(validation_losses, label=f"lr{lr}-wd{wd}")
    plt.xlabel("Epochs")
    plt.ylabel("Loss")
    plt.title("Validation Loss Across Learning Rates and Weight Decays")
    plt.legend()

    # Plot Validation Accuracy
    plt.subplot(2, 2, 2)
    plt.plot(validation_accuracies, label=f"lr{lr}-wd{wd}")
    plt.xlabel("Epochs")
    plt.ylabel("Accuracy (%)")
    plt.title("Validation Accuracy Across Learning Rates and Weight Decays")
    plt.legend()

    # Plot Test Loss
    plt.subplot(2, 2, 3)
    plt.plot(validation_losses, label=f"lr{lr}-wd{wd}")
    plt.xlabel("Epochs")
    plt.ylabel("Loss")
    plt.title("Test Loss Across Learning Rates and Weight Decays")
    plt.legend()


    # Plot Validation Accuracy
    plt.subplot(2, 2, 4)
    plt.plot(validation_accuracies, label=f"lr{lr}-wd{wd}")
    plt.xlabel("Epochs")
    plt.ylabel("Accuracy (%)")
    plt.title("Test Accuracy Across Learning Rates and Weight Decays")
    plt.legend()

    plt.savefig(f"processed_data/Centralized_lr{lr}_wd{wd}/val_test_loss_accuracy.png")

    plt.tight_layout()


# Class to handle the Shakespeare dataset in a way suitable for PyTorch.
class ShakespeareDataset(Dataset):
    def __init__(self, text, clients=None, seq_length=80, n_vocab=90):
        """
        Initialize the dataset by loading and preprocessing the data.
        Args:
        - data_path: Path to the JSON file containing the dataset.
        - clients: List of client IDs to load data for (default: all clients).
        - seq_length: Sequence length for character-level data.
        """
        self.seq_length = seq_length  # Sequence length for the model
        self.n_vocab = n_vocab  # Vocabulary size

        # Create character mappings
        self.data = list(text.values())  # Convert the dictionary values to a list


    def __len__(self):
        """
        Return the number of sequences in the dataset.
        """
        return len(self.data)

    def __getitem__(self, idx):
        """
        Retrieve the input-target pair at the specified index.
        """
        diag = self.data[idx]
        x = process_x(diag, self.seq_length, self.n_vocab)
        y = process_y(diag, self.seq_length, self.n_vocab)
        return x[0], y[0]


# Define the character-level LSTM model for Shakespeare data.
class CharLSTM(nn.Module):
    def __init__(self, n_vocab=90, embedding_dim=8, hidden_dim=256, seq_length=80, num_layers=2):
        """
        Initialize the LSTM model.
        Args:
        - n_vocab: Number of unique characters in the dataset.
        - embedding_dim: Size of the character embedding.
        - hidden_dim: Number of LSTM hidden units.
        - num_layers: Number of LSTM layers.
        - seq_length: Length of input sequences.
        """
        super(CharLSTM, self).__init__()
        self.seq_length = seq_length
        self.n_vocab = n_vocab
        self.embedding_size = embedding_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers

        # Character embedding layer: Maps indices to dense vectors.
        self.embedding = nn.Embedding(n_vocab, embedding_dim)  # Character embedding layer.

        # LSTM layers
        self.lstm_first = nn.LSTM(embedding_dim, hidden_dim, batch_first=True)  # LSTM first layer
        self.lstm_second = nn.LSTM(embedding_dim, hidden_dim, batch_first=True)  # LSTM second layer.

        # Fully connected layer: Maps LSTM output to vocabulary size.
        self.fc = nn.Linear(hidden_dim, n_vocab)  # Output layer (vocab_size outputs).

    def forward(self, x, hidden=None):
        """
        Forward pass of the model.
        Args:
        - x: Input batch (character indices).
        - hidden: Hidden state for LSTM (default: None, initialized internally).
        Returns:
        - Output logits and the updated hidden state.
        """
        # Embedding layer: Convert indices to embeddings.
        x = self.embedding(x)
        # First LSTM
        output, hidden = self.lstm_first(x, hidden)  # Process through first LSTM layer.
        # Second LSTM
        output, hidden = self.lstm_second(x, hidden)  # Process through second LSTM layer.
        # Fully connected layer: Generate logits for each character.
        output = self.fc(output)

        # Note: Softmax is not applied here because CrossEntropyLoss in PyTorch
        # combines the softmax operation with the computation of the loss.
        # Adding softmax here would be redundant and could introduce numerical instability.
        return output, hidden

    def hidden(self, batch_size):
        """
        Initializes hidden and cell states for the LSTM.
        Args:
        - batch_size: Number of sequences in the batch.
        Returns:
        - A tuple of zero-initialized hidden and cell states.
        """
        return (torch.zeros(self.num_layers, batch_size, self.hidden_dim),
            torch.zeros(self.num_layers, batch_size, self.hidden_dim))


# Define the centralized training pipeline.
def train_centralized(model, train_data, test_data, val_data, criterion, optimizer, scheduler, epochs, device, lr, wd):
    """
    Train the model on a centralized dataset.
    Args:
    - model: The LSTM model to train.
    - train_loader: DataLoader for training data.
    - test_loader: DataLoader for test data.
    - criterion: Loss function.
    - optimizer: Optimizer (SGD).
    - scheduler: Learning rate scheduler.
    - epochs: Number of training epochs.
    - device: Device to train on (CPU or GPU).
    Returns:
    - Training losses and accuracies, along with test loss and accuracy.
    """
    model.to(device)  # Move model to the device (CPU/GPU).
    model.train()  # Set the model to training mode.
    epoch_train_losses = []  # Store training loss for each epoch.
    epoch_train_accuracies = []  # Store training accuracy for each epoch.
    epoch_validation_losses = []  # Store validation loss for each epoch.
    epoch_validation_accuracies = []  # Store validation accuracy for each epoch.
    epoch_test_losses = []  # Store test loss for each epoch.
    epoch_test_accuracies = []  # Store test accuracy for each epoch.

    subfolder = f"Centralized_lr{lr}_wd{wd}"

    for epoch in range(epochs):
        total_loss = 0
        correct_predictions = 0
        total_samples = 0

        progress = tqdm(train_data, desc=f"Epoch {epoch + 1}/{epochs}")  # Track progress.

        for inputs, targets in progress:
            inputs, targets = inputs.to(device), targets.to(device)
            optimizer.zero_grad()  # Clear previous gradients.
            outputs, _ = model(inputs)  # Forward pass.
            outputs = outputs.view(-1, model.n_vocab)  # Reshape for loss computation.
            targets = targets.view(-1)  # Reshape for loss computation.
            loss = criterion(outputs, targets)  # Compute loss.
            loss.backward()  # Backpropagation.
            optimizer.step()  # Update weights.

            total_loss += loss.item()
            _, predictions = outputs.max(1)  # Get predictions.
            correct_predictions += (predictions == targets).sum().item()  # Count correct predictions.
            total_samples += targets.size(0)  # Update sample count.
            progress.set_postfix(loss=loss.item())  # Show current loss.

        train_accuracy = correct_predictions / total_samples  # Compute accuracy.
        avg_loss = total_loss / len(train_data)  # Compute average loss.
        epoch_train_losses.append(avg_loss)
        epoch_train_accuracies.append(train_accuracy)
        print(f"Epoch {epoch + 1}, Loss: {avg_loss:.4f}, Accuracy: {train_accuracy:.4f}")

        scheduler.step()  # Update learning rate (scheduler).

        # Evaluate on the validation set.
        val_loss, val_accuracy = evaluate_model(model, val_data, criterion, device)
        epoch_validation_losses.append(val_loss)
        epoch_validation_accuracies.append(val_accuracy)
        print(f"Validation Loss: {val_loss:.4f}, Validation Accuracy: {val_accuracy:.4f}")

        # Evaluate on the test set.
        test_loss, test_accuracy = evaluate_model(model, test_data, criterion, device)
        epoch_test_losses.append(test_loss)
        epoch_test_accuracies.append(test_accuracy)
        print(f"Test Loss: {test_loss:.4f}, Test Accuracy: {test_accuracy:.4f}")

        results={
                        'train_losses': epoch_train_losses,
                        'train_accuracies': epoch_train_accuracies,
                        'validation_losses': epoch_validation_losses,
                        'validation_accuracies': epoch_validation_accuracies,
                        'test_losses': epoch_test_losses,
                        'test_accuracies': epoch_test_accuracies
                    }

        save_results(model, optimizer, subfolder, epoch, lr, wd, results)

    # Final evaluation on test set
    test_loss, test_accuracy = evaluate_model(model, test_data, criterion, device)
    print(f"Final -> Test Loss: {test_loss:.4f}, Test Accuracy: {test_accuracy:.4f}")

    return epoch_train_losses, epoch_train_accuracies, epoch_validation_losses, epoch_validation_accuracies, epoch_test_losses, epoch_test_accuracies


# Evaluate model performance on a dataset.
def evaluate_model(model, data_loader, criterion, device):
    """
    Evaluate the model on a given dataset.
    Args:
    - model: Trained model.
    - data_loader: DataLoader for the evaluation dataset.
    - criterion: Loss function.
    - device: Device to evaluate on (CPU/GPU).
    Returns:
    - Average loss and accuracy.
    """
    total_loss = 0
    correct_predictions = 0
    total_samples = 0

    with torch.no_grad():  # Disable gradient computation for evaluation.
        for inputs, targets in data_loader:
            inputs, targets = inputs.to(device), targets.to(device)
            # Initialize hidden state
            state = model.hidden(inputs.size(0))
            state = (state[0].to(device), state[1].to(device))
            outputs, _ = model(inputs)
            outputs = outputs.view(-1, model.n_vocab)
            targets = targets.view(-1)
            loss = criterion(outputs, targets)  # Compute loss.
            total_loss += loss.item()
            _, predictions = outputs.max(1)
            correct_predictions += (predictions == targets).sum().item()
            total_samples += targets.size(0)

    avg_loss = total_loss / len(data_loader)  # Compute average loss.
    accuracy = (correct_predictions / total_samples ) * 100  # Compute accuracy.
    return avg_loss, accuracy


def main():
    # Dataset and training configurations
    data_path = "shakespeare.txt"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")  # Use GPU if available
    epochs = 20  # Number of epochs for centralized training -> # TODO search hyperparameters for different epochs from 20 to 200
    seq_length = 80  # Sequence length for LSTM inputs
    batch_size = 64 # batch size for centralized
    n_vocab = 90 # Character number in vobulary (ASCII)
    learning_rate = np.logspace(-3, 1, num=11) # Paper 2 give a range for learning rate's value from 10^(-3) to 10^1
    learning_rate = [1e-1, 1e-2, 1e-3, 1e-4]
    embedding_size = 8
    hidden_dim = 256
    train_split = 0.8 # In LEAF Dataset the common split used is 80/20
    momentum = 0.9
    weight_decay = [1e-3, 1e-4, 1e-5]

    # Load data
    train_data, test_data = parse_shakespeare(data_path, train_split)

    # Centralized Dataset Preparation
    train_dataset = ShakespeareDataset(train_data, seq_length=seq_length, n_vocab=n_vocab)
    test_dataset = ShakespeareDataset(test_data, seq_length=seq_length, n_vocab=n_vocab)
    train_size = int(0.8 * len(train_dataset))  # 80% of data for training
    val_size = len(train_dataset) - train_size  # 20% of data for validation
    train_dataset, validation_dataset = torch.utils.data.random_split(train_dataset, [train_size, val_size])
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(validation_dataset, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    # ====================
    # Start Centralized Training
    # ====================
    print("Starting centralized training...")

    # Saving best result
    best_result = {
        "hyperparameters": None,
        "val_accuracy": 0.0,
        "val_loss": float('inf'),
        "test_loss": float('inf'),
        "test_accuracy": 0.0
    }
    test_tot_losses = {}
    test_tot_accuracies = {}

    for lr in learning_rate:
        for wd in weight_decay:
            print(f"Learning Rate = {lr} and Weight Decay = {wd}")

            model = CharLSTM(n_vocab, embedding_size, hidden_dim, seq_length, num_layers=2)  # Initialize LSTM model
            criterion = nn.CrossEntropyLoss()  # Loss function
            optimizer = optim.SGD(model.parameters(), lr, momentum, 0, wd)  # Optimizer
            scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)  # Learning rate scheduler


            # Train and evaluate centralized model
            train_losses, train_accuracies, validation_losses, validation_accuracies, test_losses, test_accuracies = train_centralized(
                model, train_loader, test_loader, val_loader, criterion, optimizer, scheduler, epochs, device, lr, wd
            )
            test_tot_losses[f"Learning Rate = {lr} and Weight Decay = {wd}"] = test_losses
            test_tot_accuracies[f"Learning Rate = {lr} and Weight Decay = {wd}"] = test_accuracies

            if validation_losses[-1] < best_result["val_loss"]:
                best_result["hyperparameters"] = f"LR={lr} WD={wd}"
                best_result["val_accuracy"] = validation_accuracies[-1]
                best_result["val_loss"] = validation_losses[-1]
                best_result["test_loss"] = test_losses[-1]
                best_result["test_accuracy"] = test_accuracies[-1]
                print(f"Update best result -> Val Accuracy: {validation_accuracies[-1]:.4f}, Val Loss: {validation_losses[-1]:.4f}, Test Accuracy: {test_accuracies[-1]:.4f}, Test Loss: {test_losses[-1]:.4f}")

            plot_results(validation_losses, validation_accuracies, lr, wd)

    # Print best parameters found
    print(f"Best parameters:\n{best_result} ")

if __name__ == "__main__":
    main()
