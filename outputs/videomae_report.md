# CrimeWatch — VideoMAE Fine-tuning Report

**Model**: C:\Users\thesa\Desktop\Sem-7\NLP\Crime_detection\models\videomae_base  
**Parameters**: ~86 M  
**Training Time**: 27.8 minutes  
**Device**: cuda (NVIDIA GeForce RTX 2050)

## Results

| Split | Accuracy |
|---|---|
| Train (best epoch) | 100.00% |
| Validation (best)  | 45.24% |
| **Test**           | **42.86%** |

## Training History

| Epoch | Train Acc | Val Acc | Train Loss | Val Loss |
|---|---|---|---|---|
| 1 | 29.0% | 19.0% | 1.8404 | 1.7927 |
| 2 | 52.1% | 23.8% | 1.5609 | 1.6992 |
| 3 | 65.2% | 26.2% | 1.3630 | 1.6420 |
| 4 | 76.2% | 33.3% | 0.8633 | 1.5782 |
| 5 | 98.6% | 28.6% | 0.1815 | 1.7766 |
| 6 | 100.0% | 42.9% | 0.0240 | 1.6610 |
| 7 | 100.0% | 45.2% | 0.0055 | 1.7890 |
| 8 | 100.0% | 42.9% | 0.0041 | 1.8204 |

## Checkpoints

- **PyTorch state_dict**: `models/videomae_crime.pt`
- **HuggingFace format**: `models/videomae_crime_full/`

## Inference

Run:
```bash
python -m backend.inference_videomae path/to/video.mp4
```
