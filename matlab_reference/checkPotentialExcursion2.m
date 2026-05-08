function [isLimitReached,status] = checkPotentialExcursion2(...
    File,...
    potentialExcursion,...
    limitSign,...
    acceptDiff)
%% Variables
lowerLimit = File.ReferenceElectrode.LowerPotential;
upperLimit = File.ReferenceElectrode.UpperPotential;
isLimitReached = 0;
status = '';

%% Function
% Water window
potentialExcursion_round = round(potentialExcursion,3);
switch limitSign
% Lower limit
    case -1
        acceptLimitMin = lowerLimit - acceptDiff;
        acceptLimitMax = lowerLimit + acceptDiff;
        isAboveMin = any(potentialExcursion_round > acceptLimitMin);
        isBelowMax = any(potentialExcursion_round < acceptLimitMax);
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