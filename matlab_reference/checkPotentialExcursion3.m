function [isLimitReached,status] = checkPotentialExcursion3(File,acceptDiff)
%% Variables
% Index
channel_arr = [File.Data.Channel];
channelNum = length(channel_arr);
capture_arr = [File.Data(channelNum).Capture.Index];
captureNum = length(capture_arr);

% Values
amplitude = File.Data(channelNum).Capture(captureNum).Amplitude;
potentialExcursion_arr = File.Data(channelNum).Capture(captureNum).PotentialExcursion;
potentialExcursion = potentialExcursion_arr(1);
potentialExcursion_round = round(potentialExcursion,3);
amplitude_sign = sign(amplitude);
lowerLimit = File.ReferenceElectrode.LowerPotential;
upperLimit = File.ReferenceElectrode.UpperPotential;
isLimitReached = 0;
status = '';

%% Function
% Water window
switch amplitude_sign
% Lower limit
    case {-1,0}
        acceptLimitMin = lowerLimit - acceptDiff;
        acceptLimitMax = lowerLimit + acceptDiff;
        isAboveMin = potentialExcursion_round > acceptLimitMin;
        isBelowMax = potentialExcursion_round < acceptLimitMax;
        if isAboveMin && isBelowMax
            isLimitReached = -1;
            status = 'Cathodic potential limit reached';
        end
    case 1
        acceptLimitMin = upperLimit - acceptDiff;
        acceptLimitMax = upperLimit + acceptDiff;
        isAboveMin = any(potentialExcursion_round > acceptLimitMin);
        isBelowMax = any(potentialExcursion_round < acceptLimitMax);
        if isAboveMin && isBelowMax
            isLimitReached = 1;
            status = 'Anodic potential limit reached';
        end
end

end