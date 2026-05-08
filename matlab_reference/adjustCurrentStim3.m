function [currentStim_new,currentChange] = adjustCurrentStim3(File,channelNum,captureNum)
%% Constants
CURRENT_COMPLIANCE = 1e3;
CURRENT_MIN = 0.1;
% Max potential and lower potential limit difference
MAX_POTENTIAL_DIFF_THRESH = [...
    600e-3,...
    500e-3,...
    400e-3,...
    200e-3,...
    100e-3,...
    50e-3,...
    20e-3,...
    10e-3,...
    5e-3,...
    1e-3];

% Current increment (uA)
CURRENT_INCREMENT = [...
    80,...
    60,...
    40,...
    20,...
    10,...
    8,...
    6,...
    2,...
    1,...
    0.5];

% Current decrement (uA)
CURRENT_DECREMENT = -[...
    20,...
    15,...
    10,...
    6.1,...
    3.1,...
    2.5,...
    2.1,...
    0.6,...
    0.3,...
    0.2];

%% Variable
% Values
scale = 1;
stimType = File.Parameters.Type;
isTargetMax = strcmpi(stimType,'MAX') || strcmpi(stimType,'POST');
isTargetStep = strcmpi(stimType,'STEP');

% Amplitude
amplitude1 = File.Data(channelNum).Capture(captureNum).Amplitude;
amplitude1_sign = sign(amplitude1);
currentStim = abs(amplitude1);
currentStim_new = currentStim;

% Potential Excursion
if amplitude1_sign < 0
    potentialLimit = File.ReferenceElectrode.LowerPotential;
else
    potentialLimit = File.ReferenceElectrode.UpperPotential;
end
potentialExcursion_arr = File.Data(channelNum).Capture(captureNum).PotentialExcursion;
potentialExcursion = potentialExcursion_arr(1);

% Surface Area
try
    surfaceArea = File.SurfaceArea;
catch
    surfaceArea = File.Data(channelNum).SurfaceArea;
end
if surfaceArea >= 1000 && surfaceArea <= 2000
    scale = 1;
end
if surfaceArea >= 2000
    scale = 2;
end
if surfaceArea >= 50 && surfaceArea <= 200
    scale = 0.2;
end
if surfaceArea > 200 && surfaceArea < 1000
    scale = 0.5;
end

% Duty Cycle
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
if phaseWidth1 < 100
    stimRate = File.Parameters.StimulationRate;
    stimPeriod = 1 / stimRate;
    dutyCycle = phaseWidth1 * 1e-6 / stimPeriod;
%     interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
%     phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
%     pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
%     dutyCycle_max = pulseWidth * 1e-6 / stimPeriod;
    if dutyCycle >= 0.3
        scale = scale * 1;
    elseif dutyCycle >= 0.2
        scale = scale * 2;
    elseif dutyCycle >= 0.1
        scale = scale * 3;
    else
        scale = scale * 5;
    end
else
    subjectSelect = File.Subject;
    isAnimal = contains(subjectSelect,'A');
    if ~isAnimal
        scale = scale * 1.5;
    end
end

% Step Size
if isTargetMax
    chargePhaseLimit = inf;
    stepSize = 0;
elseif isTargetStep
    amplitude1_arr = File.Parameters.Amplitude1;
    amplitudeTarget = amplitude1_arr(channelNum);
    [chargePhaseLimit,~] = getCharge(amplitudeTarget,phaseWidth1,0);
    stepSize = File.Parameters.StepSize;
else
    chargePhaseLimit = 4;
    stepSize = 0.5;
end

% Current Values
currentInc = CURRENT_INCREMENT * scale;
currentDec = CURRENT_DECREMENT * scale;

%% Function
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
fprintf('\n');
if chargePhaseLimit == 0 || isinf(chargePhaseLimit)   % stimulating to max cathodal limit
    fprintf('Adjusting current amplitude %g uA...',currentStim);
    fprintf('%0.3f V...',potentialExcursion_diff_mag);
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
        case 1 % above potential
            fprintf('incrementing by ');
            currentChange = currentInc(idx);
        case -1 % below potential
            fprintf('decrementing by ');
            currentChange = currentDec(idx);   
    end
    currentChange = round(currentChange,1);
    if currentChange == 0
        currentChange = CURRENT_MIN * limitPotential_sign;
    end
%     if potentialExcursion_diff_mag > 1
%         currentChange = currentChange / 2;
%     end
    currentStim_new = currentStim + currentChange;
    if currentStim_new > CURRENT_COMPLIANCE
        currentStim_new = CURRENT_COMPLIANCE;
        currentChange = currentStim_new - currentStim;
    end
    while currentStim_new <= 0
        currentChange = round(currentChange * 0.9,1);
        currentStim_new = currentStim + currentChange;
        if abs(currentChange) < CURRENT_MIN
            currentChange = CURRENT_MIN * limitPotential_sign;
            currentStim_new = currentStim + currentChange;
            break;
        end
    end
    fprintf('%g uA to...',abs(currentChange));

else % stimulating to charge-per-phase target
    fprintf('Adjusting current amplitude %g uA...',currentStim);
    fprintf('incrementing by %g uA to...',stepSize);
    currentStim_new = currentStim_new + stepSize;   % increment current by step size
    currentChange = stepSize;
end
currentStim_new = abs(currentStim_new);
fprintf('%g uA\n',currentStim_new);
% potentialExcursionTresh = MAX_POTENTIAL_DIFF_THRESH(idx);
% fprintf('Voltage difference threshold: %.3f V',potentialExcursionTresh);

end