function [lowerLimit_new,upperLimit_new] = changePotential(lowerLimit,upperLimit,referenceElectrode)
%% Constants
INPUTDLG_SIZE = [1 70];
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_QUIT = 'Quit';
% Dialog
opts.Default = 'tex';
opts.Interpreter = 'tex';

%% Script
confirmPotential = false;
while ~confirmPotential
    % Initial Potential
    prompt = sprintf('Potential limits versus %s: %.3f and %.3f\nEnter used potentials (V) {\\bf(Use comma for more values)}',referenceElectrode,lowerLimit,upperLimit);
    inputTitle = sprintf('Potential versus {\\bf%s}',referenceElectrode);
    lowerLimit_char = num2str(lowerLimit);
    upperLimit_char = num2str(upperLimit);
    inputDefault = {[lowerLimit_char ', ' upperLimit_char]};
    inputPotential_cell = inputdlg(prompt,inputTitle,INPUTDLG_SIZE,inputDefault,opts);
    if ~isempty(inputPotential_cell)
        inputPotential = inputPotential_cell{1};
        inputPotential = erase(inputPotential,' ');
        inputPotential = strrep(inputPotential,'and',',');
        inputPotential_char = strsplit(inputPotential,',');
        inputPotential_fix = strrep(inputPotential_char,' ','');
        potential_new = str2double(inputPotential_fix);
    else
        return;
    end
    
    % New Potential
    lowerLimit_new = potential_new(1);
    upperLimit_new = potential_new(2);
    questInput = sprintf( ...
        'Normal potential versus {\\bf%s} (V): {\\bf%.3f}, {\\bf%.3f}', ...
        referenceElectrode,lowerLimit,upperLimit);
    questOutput = sprintf( ...
        'New potential versus {\\bf%s} (V): {\\bf%.3f}, {\\bf%.3f}', ...
        referenceElectrode,lowerLimit_new,upperLimit_new);
    questConv = {questInput,questOutput};
    if lowerLimit == lowerLimit_new && upperLimit == upperLimit_new
        questTitle = 'Confirm Potential Same';
    else
        questTitle = 'Confirm Potential Change';
    end
    opts.Default = 'Confirm';
    questPotential = questdlg(questConv,questTitle,BUTTON_CONFIRM,BUTTON_TRY,opts);
    switch questPotential
        case BUTTON_CONFIRM
            confirmPotential = true;
        case BUTTON_TRY
            continue;
        otherwise
            return;
    end
end
