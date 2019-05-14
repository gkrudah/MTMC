function embed(opts)

tic % modified by ha

net = opts.net;

cur_dir = pwd;
cd src/triplet-reid

datasets = {'data/duke_test.csv', 'data/duke_query.csv', 'data/duke_train.csv'};

for k = 1:length(datasets)
    dataset = datasets{k};
    
    command = strcat(opts.python3, ' embed.py' , ...
        sprintf(' --experiment_root %s', net.experiment_root), ...
        sprintf(' --image_root %s', net.image_root), ...
        sprintf(' --dataset %s', dataset));
    
    system(command);

    
end

cd(cur_dir)
toc % modified by ha