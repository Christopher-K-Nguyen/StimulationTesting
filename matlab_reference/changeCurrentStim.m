function [currentStim_new,currentChange_new] = changeCurrentStim(File)
%% Constants
CURRENT_COMPLIANCE = 1e3;
CURRENT_MIN = 0.03;

% Max potential and lower potential limit difference
ACCEPT_DIFF = 0.02;
MAX_POTENTIAL_DIFF_THRESH = [ ...
    700e-3, ...
    600e-3, ...
    500e-3, ...
    400e-3, ...
    200e-3, ...
    100e-3, ...
    50e-3, ...
    20e-3, ...
    10e-3, ...
    5e-3, ...
    1e-3];

% Current increment (uA)
% CURRENT_INCREMENT = [ ...
%     50, ...
%     30, ...
%     24, ...
%     12, ...
%     10, ...
%     8, ...
%     4, ...
%     2, ...
%     1, ...
%     0.5];
CURRENT_INCREMENT = [ ...
    90, ...
    60, ...
    36, ...
    28, ...
    20, ...
    12, ...
    10, ...
    8, ...
    4, ...
    1, ...
    0.03];

% Current decrement (uA)
CURRENT_DECREMENT = -[...
    100, ...
    80, ...
    60, ...
    40, ...
    20, ...
    8, ...
    4, ...
    1, ...
    0.1, ...
    0.03];

%% Variable
% Environment
environment = File.Parameters.Environment;
isAnimal = contains2(environment,'Animal');

% Index
channel_arr = [File.Data.Channel];
numOfChannels = length(channel_arr);
channelNum = channel_arr(numOfChannels);
capture_arr = [File.Data(channelNum).Capture.Index];
captureNum = length(capture_arr);

% Pattern
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
% interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
% phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
% pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;

% Amplitude
amplitude_arr = [File.Data(channelNum).Capture.Amplitude];
currentStim_arr = abs(amplitude_arr);
amplitude = amplitude_arr(captureNum);
amplitude_sign = sign(amplitude);
currentStim = abs(amplitude);
currentStim_new = currentStim;

% Capacitance
capacitance = File.Data(channelNum).Capture(captureNum).Capacitance;

% Access Resistance
accessResistance_arr = File.Data(channelNum).Capture(captureNum).AccessResistance;
accessResistance = accessResistance_arr(1);

% Potential Excursion
% potentialExcursion_arr = File.Data(channelNum).Capture(captureNum).PotentialExcursion;
lowerPotential = File.ReferenceElectrode.LowerPotential;
upperPotential = File.ReferenceElectrode.UpperPotential;

% Change
targetCharge = File.Test.Value;
targetAmplitude = ((targetCharge * 1e-9) / (phaseWidth1 * 1e-6)) / 1e-6;
isTargetMax = isinf(targetCharge);
hasTarget = isnumeric(targetAmplitude) || isTargetMax;
stepSize = File.Test.StepSize;
currentChange = 0;

% Scale
surfaceArea = File.Data(channelNum).SurfaceArea;
scale = 1;
if surfaceArea >= 1000 && surfaceArea <= 2000
    scale = 1;
elseif surfaceArea >= 2000
    scale = 2;
elseif surfaceArea > 200 && surfaceArea < 1000
    scale = 0.5;
elseif surfaceArea >= 50 && surfaceArea <= 200
    scale = 0.2;
elseif surfaceArea < 50
    scale = 0.1;
end

if ~isAnimal
    scale = scale * 1.5;
else
    scale = scale * 1;
end

if accessResistance < 2 && isAnimal
    scale = scale * 0.25;
elseif accessResistance > 10 && isAnimal
    scale = scale * 0.5;
elseif (accessResistance < 0.5 || accessResistance > 2.1) && ~isAnimal
    scale = scale * 0.5;
elseif any(capacitance > 120)
    scale = scale * 0.2;
end
if isAnimal
    earlyCaptureNum_check = 4;
else
    if isRateTest
        earlyCaptureNum_check = 3;
    else
        earlyCaptureNum_check = 5;
    end
end
% if captureNum < earlyCaptureNum_check-3
%     scale = scale * 0.25;
% elseif captureNum < earlyCaptureNum_check-2
%     scale = scale * 0.3;
% elseif captureNum < earlyCaptureNum_check-1
%     scale = scale * 0.4;
% elseif captureNum <= earlyCaptureNum_check
%     scale = scale * 0.5;
% end
if captureNum < earlyCaptureNum_check
    scale = scale * 0.5;
end
currentInc = CURRENT_INCREMENT * scale;
currentDec = CURRENT_DECREMENT * scale;

%% Function
if hasTarget
    fprintf('\n');
    fprintf('Changing current amplitude...\n');
    startTime = tic;
    % Check
    potentialExcursion_arr = zeros(captureNum,1);
    for capture_idx = 1:captureNum
        potentialExcursion_arr = File.Data(channelNum).Capture(capture_idx).PotentialExcursion;
        switch amplitude_sign
            case {-1,0}
                potentialExcursion = potentialExcursion_arr(1);
            case 1
                potentialExcursion = potentialExcursion_arr(2);
        end
        potentialExcursion_arr(capture_idx) = potentialExcursion;
    end
%     potentialExcursion = potentialExcursion_arr(captureNum);


    switch amplitude_sign
        case {-1,0}
            potentialLimit = lowerPotential;
        case 1
            potentialLimit = upperPotential;
    end
    potentialExcursionMin = potentialLimit - ACCEPT_DIFF;
    potentialExcursionMax = potentialLimit + ACCEPT_DIFF;
    switch amplitude_sign
        case {-1,0}
            over_idx  = potentialExcursion_arr < potentialExcursionMin;
            under_idx = potentialExcursion_arr > potentialExcursionMax;
        case 1
            over_idx = potentialExcursion_arr > potentialExcursionMax;
            under_idx = potentialExcursion_arr < potentialExcursionMin;       
    end
    currentStimOver_arr = currentStim_arr(over_idx);
    currentStimUnder_arr = currentStim_arr(under_idx);
    currentStimOver = min(currentStimOver_arr);
    currentStimUnder = max(currentStimUnder_arr);
    hasCurrentStimUnder = ~isempty(currentStimUnder);
    hasCurrentStimOver = ~isempty(currentStimOver);

    % Distance to lower limit
    potentialExcursion_diff = potentialExcursion - potentialLimit;
    potentialExcursion_diff_mag = abs(potentialExcursion_diff);
    % fprintf('Voltage difference from potential limit: %.3f V\n',potentialExcursionDiff_mag);
    if potentialLimit < 0
        limitPotential_sign = -1;
        if potentialExcursion > potentialLimit
            potentialExcursion_diff_sign = 1;
        else
            potentialExcursion_diff_sign = -1;
        end
    else
        limitPotential_sign = 1;
        if potentialExcursion < potentialLimit
            potentialExcursion_diff_sign = 1;
        else
            potentialExcursion_diff_sign = -1;
        end
    end
    % fprintf('Max Potential sign: %d\n',potentialExcursionDiff_sign);
    % potentialExcursionDiff_sign = sign(potentialExcursionDiff);
    fprintf('\t');
    switch potentialExcursion_diff_sign
        case 1
            fprintf('Stimulation BELOW target: %0.3f V\n',potentialExcursion_diff_mag);
        case -1
            fprintf('Stimulation ABOVE target: %0.3f V\n',potentialExcursion_diff_mag);
    end
    fprintf('\t');
    if isTargetMax   % stimulating to max cathodal limit
        %     fprintf('Potential difference: %0.3f V\n',potfprintf('\t');entialExcursion_diff_mag);
        fprintf('Changing current amplitude %.2f uA...',currentStim);
        if potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(1)     % above 500 mV
            idx = 1;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(2) % above 400 mV
            idx = 2;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(3) % above 200 mV
            idx = 3;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(4) % above 100 mV
            idx = 4;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(5) % above 50 mV
            idx = 5;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(6) % above 10 mV
            idx = 6;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(7) % above 5 mV
            idx = 7;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(8) % above 1 mV
            idx = 8;
        elseif potentialExcursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(9) % above 1 mV
            idx = 9;
        end
        switch potentialExcursion_diff_sign
            case 1 % below target
                currentChange_sign = 1;
                currentChange_sign_char = '+';
                currentChange = currentInc(idx);
            case -1 % above target
                currentChange_sign = -1;
                currentChange_sign_char = '-';
                currentChange = currentDec(idx);
        end
        currentChange = roundStim(currentChange);
        %     if potentialExcursion_diff_mag > 1
        %         currentChange = currentChange / 2;
        %     end

        currentStim_new = currentStim + currentChange;
        if currentStim_new < 0
            currentStim_new = CURRENT_MIN;
            currentChange = currentStim_new - currentStim;
        end
        isCurrentGood = false;
        count = 0;  
        startTime_check = tic;
        while ~isCurrentGood
            count = count + 1;
            idx_shift = 5;
            if captureNum > idx_shift
                check_idx = captureNum - idx_shift;
                currentStim_check = currentStim_arr(check_idx:captureNum);
                [~,modeCount] = mode(currentStim_check);
                isAmplitudeRepeat = any(modeCount > 1);
            else
                isAmplitudeRepeat = false;
            end
            if hasCurrentStimOver
                isCurrentStimOver = currentStim_new >= currentStimOver;
            else
                isCurrentStimOver = false;
            end
            if hasCurrentStimUnder
                isCurrentStimUnder = currentStim_new <= currentStimUnder;
            else
                isCurrentStimUnder = false;
            end
            isCurrentGood = ~isCurrentStimOver && ~isCurrentStimUnder ...
                && ~isAmplitudeRepeat;
            if isCurrentGood
                currentStim_new = roundStim(currentStim + currentChange);
%                 currentChange = currentStim_new - currentStim;
                break;
            elseif (hasCurrentStimOver && hasCurrentStimUnder) ...
                    || count > 1000 ...
                    || toc(startTime_check) > 1
                currentStim_outside = [currentStimUnder currentStimOver];
                currentStim_new = roundStim(mean(currentStim_outside));
                break;
            elseif isCurrentStimOver
                if count > 100
                    currentChange = currentChange - CURRENT_MIN;
                else
                    currentChange = currentChange * 0.9;
                end
                currentStim_new = roundStim(currentStim + currentChange);
                currentChange = currentStim_new - currentStim;
            elseif isCurrentStimUnder
                if count > 100
                    currentChange = currentChange + CURRENT_MIN;
                else
                    currentChange = currentChange * 1.1;
                end
                currentStim_new = roundStim(currentStim + currentChange);
                currentChange = currentStim_new - currentStim;
            end
        end
        currentChange = currentStim_new - currentStim;
%         currentChange_fix_sign = -1;
%         while ismember(currentStim_new,currentStim_arr)
%             currentStim_new = currentStim_new + currentChange_fix_sign * 0.1;
%             if currentStim_new == 0
%                 if currentChange_fix_sign == -1
%                     currentChange_fix_sign = 1;
%                     currentStim_new = currentStim;
%                 else
%                     currentStim_new = currentStim + currentChange;
%                     break;
%                 end
%             end
%         end
        
        if abs(currentChange) < CURRENT_MIN
            currentChange = CURRENT_MIN * limitPotential_sign;
            currentStim_new = currentStim + currentChange;
        end
        if currentStim_new >= CURRENT_COMPLIANCE-2
            currentStim_new = CURRENT_COMPLIANCE;
            currentChange = currentStim_new - currentStim;
        end
        currentChange_mag = abs(currentChange);
        currentChange_new = currentChange_sign * currentChange_mag;
        fprintf('%s%.2f uA...',currentChange_sign_char,currentChange_mag);

    else % stimulating to charge-per-phase target
        fprintf('Changing current amplitude %.2f uA...',currentStim);
        fprintf('+%.2f uA...',stepSize);
        currentStim_new = currentStim_new + stepSize;   % increment current by step size
        currentChange_new = amplitude_sign * stepSize;
    end
    currentStim_new = abs(currentStim_new);
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2f uA\n',currentStim_new);
    fprintf('\tTime elapsed: %.2f %s\n',endTime,unit);
    % potentialExcursionTresh = MAX_POTENTIAL_DIFF_THRESH(idx);
    % fprintf('Voltage difference threshold: %.3f V',potentialExcursionTresh);
else
    currentStim_new = currentStim;
    currentChange_new = 0;
end

end