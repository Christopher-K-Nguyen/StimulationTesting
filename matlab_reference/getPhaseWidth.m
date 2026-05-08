function phaseWidth = getPhaseWidth(time,current)
if max(time) < 1
    time = time / 1e-6;
end
current_filt = lowpass(current,0.0001);
current_diff = diff(current_filt);
time_diff = diff(time);
derivativeI_raw = current_diff ./ time_diff;
derivativeI = lowpass(derivativeI_raw,0.0001);
peakNeg_thresh = max(-derivativeI) * 0.8;
peakPos_thresh = max(derivativeI) * 0.8;
[~,peaksNeg_idx] = findpeaks(derivativeI,'MinPeakHeight',peakNeg_thresh);
[~,peaksPos_idx] = findpeaks(-derivativeI,'MinPeakHeight',peakPos_thresh);
phaseWidth1_start_idx = peaksNeg_idx(1);
phaseWidth1_end_idx = peaksPos_idx(1);
period = time(phaseWidth1_end_idx) - time(phaseWidth1_start_idx);
phaseWidth = round(abs(period));

end