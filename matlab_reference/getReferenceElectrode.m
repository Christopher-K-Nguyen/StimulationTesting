function [File,isQuit] = getReferenceElectrode(File,varargin)
%% Constants
LISTDLG_SIZE = [160 100];
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_QUIT = 'Quit';
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';

%% Variables
numOfVar = length(varargin);
if numOfVar > 1
    type = varargin{1};
else
    type = [];
end
isQuit = false;
refElectrode = '';
lowerPotential = File.Parameters.WorkingElectrode.LowerPotential;
upperPotential = File.Parameters.WorkingElectrode.UpperPotential;
waterWindow = [lowerPotential upperPotential];
environment = File.Parameters.Environment;
isAnimal = contains2(environment,{'animal','rat','mouse','vivo'});
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,{'TP'}) && ~isPTP;
isCG = contains2(configID,'CG');
isPartial = isPBP || isPTP;
workingElectrode = File.Parameters.WorkingElectrode.Type;
if contains2(workingElectrode,{'IR','IRO'})
    workingPotential = 0.1;
elseif contains2(workingElectrode,'TiN')
    workingPotential = 0;
elseif contains2(workingElectrode,'PEDOT')
    workingPotential = 0.2;
end


% Reference
refList = { ...
    'Ag|AgCl', ...
    'Pt', ...
    'PtIr', ...
    'SS', ...
    'Ir', ...
    'Ti', ...
    'W', ...
    'Au'};
% Potential
if isAnimal
    refPotential_arr = [ ...
        0, ...
        -0.130, ...
        0, ...
        0, ...
        0, ...
        0, ...
        0, ...
        0];
else
    refPotential_arr = [ ...
        0, ...
        0.300, ...
        0.200, ...
        0.000, ...
        0.100, ...
        0.000, ...
        -0.300, ...
        0.300];
end

if isMP || isPartial
    counterList = { ...
        'Pt', ...
        'PtIr', ...
        'SS', ...
        'Ir', ...
        'Ti', ...
        'W', ...
        'Au'};
else
    counterList = { ...
        workingElectrode, ...
        'Pt', ...
        'PtIr', ...
        'SS', ...
        'Ir', ...
        'Ti', ...
        'W', ...
        'Au'};
end
if isAnimal
    counterPotential_arr = [ ...
        0, ...
        0, ...
        0, ...
        0, ...
        0, ...
        0,...
        0, ...
        0];
else
    counterPotential_arr = [ ...
        workingPotential, ...
        0.300, ...
        0.200, ...
        0.000, ...
        0.100, ...
        0.000, ...
        -0.300, ...
        0.300];
end

ReferenceElectrode = struct( ...
    'Type',[], ...
    'OpenCircuitPotential',[], ...
    'LowerPotential',[], ...
    'UpperPotential',[]);
CounterElectrode = ReferenceElectrode;

if isempty(type)
    %% Function
    fprintf('Select reference electrode...');

    confirmPotential = false;
    while ~confirmPotential
        % Initial Reference
        [refList_idx,~] = listdlg( ...
            'PromptString','Select reference electrode', ...
            'ListString',refList, ...
            'SelectionMode','single', ...
            'InitialValue',1, ...
            'ListSize',LISTDLG_SIZE);
        if ~isempty(refList_idx)
            refElectrode = refList{refList_idx};
            refPotential = refPotential_arr(refList_idx);
        else
            isQuit = true;
            break;
        end

        % New Potential
        windowRef = waterWindow - refPotential;
        lowerPotential_new = windowRef(1);
        upperPotential_new = windowRef(2);

        % Change
        waterWindow_use = sprintf( ...
            'Potential versus Ag|AgCl: %.3f to %.3f V', ...
            lowerPotential,upperPotential);
        change_use = sprintf( ...
            'Potential versus {\\bf%s}: %.3f to %.3f V', ...
            refElectrode, ...
            lowerPotential_new,upperPotential_new);
        questInput = {waterWindow_use,change_use};
        questPotential = questdlg( ...
            questInput, ...
            'Confirm Reference Electrode', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
            opts);
        switch questPotential
            case BUTTON_CONFIRM
                confirmPotential = true;
                fprintf('%s\n',refElectrode);
            case BUTTON_TRY
                continue;
            case BUTTON_QUIT
                isQuit = true;
                break;
        end
    end
else
    switch upper(type)
        case {'SILVER','AG','AG|AGCL'}
            type = 'Ag|AgCl';
            % case {'IRIDIUM','IR'}
            %     type = 'Ir';
        case {'PLATINUM','PLAT','PT'}
            type = 'Pt';
        case {'PLATINUM-IRIDIUM','PLATINUM IRIDIUM','PLATINUMIRIDIUM','PTIR'}
            type = 'PtIr';
        case {'SS','SST','STAINLESSSTEEL','STAINLESS STEEL','STAINLESS','STEEL'}
            type = 'SS';
    end
    ref_idx = find(containsi(refList,type));
    refElectrode = refList{ref_idx};
    refPotential = refPotential_arr(ref_idx);
    windowRef = waterWindow - refPotential;
    lowerPotential_new = windowRef(1);
    upperPotential_new = windowRef(2);
end
if isQuit
    return;
end

isSilver = contains2(refElectrode,'Ag');
counterElectrode = counterList{1};
counterPotential = counterPotential_arr(1);
if isBP || isTP || isCG
    questRefenceCounterSame = BUTTON_NO;
    windowCounter = waterWindow;
    lowerPotential_alt = windowCounter(1);
    upperPotential_alt = windowCounter(2);
else
    windowCounter = waterWindow - counterPotential;
    lowerPotential_alt = windowCounter(1);
    upperPotential_alt = windowCounter(2);
    confirmCounter = false;
    while ~confirmCounter
        fprintf('Selecting counter electrode...');
        confirmChoice = false;
        while ~confirmChoice
            if ~isSilver
                promptCounter = sprintf('Is {\\bf%s} also your counter/return electrode?',refElectrode);
                questRefenceCounterSame = questdlg( ...
                    promptCounter, ...
                    'Counter Electrode', ...
                    BUTTON_YES,BUTTON_NO,BUTTON_YES);

                switch questRefenceCounterSame
                    case BUTTON_YES
                        choice = 'Same';
                    case BUTTON_NO
                        choice = 'Different';
                end

                questInput = sprintf('Counter Electrode: %s',choice);
                questConfirmChoice = questdlg( ...
                    questInput, ...
                    'Counter Electrode', ...
                    BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
                    opts);
                switch questConfirmChoice
                    case BUTTON_CONFIRM
                        confirmChoice = true;
                    case BUTTON_TRY
                        continue;
                    case BUTTON_QUIT
                        isQuit = true;
                        break;
                end
            else
                questRefenceCounterSame = BUTTON_NO;
                confirmChoice = true;
            end
        end

        switch questRefenceCounterSame
            case BUTTON_YES
                counterElectrode = refElectrode;
                confirmCounter = true;
                fprintf('%s\n',counterElectrode);
            case BUTTON_NO
                % Initial Counter
                if isPartial || isCG
                    selectionMode = 'multiple';
                else
                    selectionMode = 'single';
                end
                [counterList_idx,~] = listdlg( ...
                    'PromptString','Select counter/return electrode', ...
                    'ListString',counterList, ...
                    'SelectionMode',selectionMode, ...
                    'InitialValue',1, ...
                    'ListSize',LISTDLG_SIZE);
                if ~isempty(counterList_idx)
                    numOfCounters = length(counterList_idx);
                    if numOfCounters > 1
                        counterList_idx_fix = numOfCounters;
                        counterElectrode_cell = counterList(counterList_idx);
                        counterElectrode = strjoin(counterElectrode_cell,' + ');
                    else
                        counterList_idx_fix = counterList_idx;
                        counterElectrode = counterList{counterList_idx_fix};
                    end
                    counterPotential = counterPotential_arr(counterList_idx_fix);
                    windowCounter = waterWindow - counterPotential;
                    lowerPotential_alt = windowCounter(1);
                    upperPotential_alt = windowCounter(2);
                else
                    isQuit = true;
                    break;
                end

                % Change
                questInput = sprintf('Counter/Return Electrode: {\\bf%s}',counterElectrode);
                questCounter = questdlg( ...
                    questInput, ...
                    'Confirm Counter Electrode', ...
                    BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
                    opts);
                switch questCounter
                    case BUTTON_CONFIRM
                        confirmCounter = true;
                        fprintf('%s\n',counterElectrode);
                    case BUTTON_TRY
                        continue;
                    case BUTTON_QUIT
                        isQuit = true;
                        break;
                end
        end
    end
    if isQuit
        return;
    end
end

%% Store
% Reference
ReferenceElectrode.Type = refElectrode;
ReferenceElectrode.OpenCircuitPotential = refPotential;
if contains2(refElectrode,'Ag')
    ReferenceElectrode.LowerPotential = lowerPotential;
    ReferenceElectrode.UpperPotential = upperPotential;
    CounterElectrode.LowerPotential = lowerPotential_new;
    CounterElectrode.UpperPotential = upperPotential_new;
else
    ReferenceElectrode.LowerPotential = lowerPotential_new;
    ReferenceElectrode.UpperPotential = upperPotential_new;
end
File.Parameters.ReferenceElectrode = ReferenceElectrode;

% Counter
if ~isSilver
    ref = counterElectrode;
else
    ref = 'Ag|AgCl';
end
switch questRefenceCounterSame
    case BUTTON_YES
        [lowerLimit_new,upperLimit_new] = changePotential( ...
            lowerPotential_new,upperPotential_new,ref);
        ReferenceElectrode.LowerPotential = lowerLimit_new;
        ReferenceElectrode.UpperPotential = upperLimit_new;
        CounterElectrode = ReferenceElectrode;
    case BUTTON_NO
        % if isPTP
        %     CounterElectrode.Type = [workingElectrode ' + ' counterElectrode];
        % else
        CounterElectrode.Type = counterElectrode;
        % end
        if ~isSilver
            [lowerLimit_new,upperLimit_new] = changePotential( ...
                lowerPotential_alt,upperPotential_alt,ref);
        else
            [lowerLimit_new,upperLimit_new] = changePotential( ...
                lowerPotential,upperPotential,ref);
        end
        ReferenceElectrode.LowerPotential = lowerLimit_new;
        ReferenceElectrode.UpperPotential = upperLimit_new;
        CounterElectrode.OpenCircuitPotential = counterPotential;
        CounterElectrode.LowerPotential = lowerLimit_new - counterPotential;
        CounterElectrode.UpperPotential = upperLimit_new - counterPotential;
end
File.Parameters.ReferenceElectrode = ReferenceElectrode;
File.Parameters.CounterElectrode = CounterElectrode;

end